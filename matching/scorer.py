"""Score jobs against a user's criteria with the model behind their key."""
import os
import re

from pydantic import BaseModel, Field

from llm import Credentials, LLMError, complete_json

# Only jobs at or above this score make it into a digest. Configurable because
# a narrow search on a quiet day can leave the bar higher than anything clears.
MATCH_THRESHOLD = int(os.getenv("MATCH_THRESHOLD", "70"))

# Each posting scored costs one LLM call, and a free key is rate-limited, so a
# day's sourcing (thousands of postings) can't all be scored: most calls would
# be throttled to a 0 and nothing would match. Cheaply rank by title relevance
# first and only spend calls on the most promising. Override with MAX_JOBS_TO_SCORE.
MAX_TO_SCORE = int(os.getenv("MAX_JOBS_TO_SCORE", "50"))

_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def prefilter_jobs(all_jobs: list[dict], criteria: dict, limit: int) -> list[dict]:
    """The postings most worth an LLM call, cheaply and locally.

    Ranks each posting by how much its title overlaps the target titles and
    include-keywords, drops anything hitting an exclude-keyword, and keeps the
    top `limit`. With no titles set, keeps the first `limit` as they came.
    """
    title_words: set[str] = set()
    for title in criteria.get("job_titles") or []:
        title_words |= _words(title)
    include = {k.lower() for k in criteria.get("keywords_include") or []}
    exclude = {k.lower() for k in criteria.get("keywords_exclude") or []}

    if not title_words and not include:
        return [j for j in all_jobs if j.get("id")][:limit]

    scored: list[tuple[int, int, dict]] = []
    for i, job in enumerate(all_jobs):
        if not job.get("id"):
            continue
        haystack = f"{job.get('title', '')} {job.get('company', '')}".lower()
        if exclude and any(term in haystack for term in exclude):
            continue
        title_hits = len(title_words & _words(job.get("title", "")))
        keyword_hits = sum(1 for term in include if term in haystack)
        rank = title_hits * 3 + keyword_hits
        if rank == 0:
            continue
        # -i keeps the original order stable among equal ranks (sort is desc).
        scored.append((rank, -i, job))

    scored.sort(reverse=True)
    return [job for _, _, job in scored[:limit]]


SYSTEM = """You score job postings against a candidate's stated criteria.

Be honest and specific. A high score means the candidate would plausibly want to
apply, not merely that the posting is a real job. Weigh title fit, seniority,
location and remote policy, and salary against what the candidate asked for.

Scoring bands:
  90-100  excellent match
  70-89   good match
  50-69   partial match
  0-49    poor match

The reason is one sentence, read by the candidate in their morning email. Name
the concrete thing that drove the score: the title, the location, the salary,
a specific skill. Write it the way you'd tell a friend.

  Good: "Remote and the salary band clears your floor, but it's a level below
         what you asked for."
  Bad:  "This role aligns well with your professional background and offers
         exciting opportunities for growth."

No filler adverbs, no em dashes, no "aligns with" or "leverages"."""


class JobScore(BaseModel):
    score: int = Field(ge=0, le=100, description="How well this job matches, 0-100.")
    reason: str = Field(description="One sentence explaining the score.")


def _criteria_block(criteria: dict) -> str:
    salary_max = criteria.get("max_salary") or "open"
    return f"""Target titles: {criteria.get('job_titles') or 'any'}
Locations: {criteria.get('locations') or 'any'}
Remote preference: {criteria.get('remote_preference') or 'any'}
Salary range: {criteria.get('min_salary') or 0} to {salary_max}
Seniority: {criteria.get('seniority_levels') or 'any'}
Must include keywords: {criteria.get('keywords_include') or 'none'}
Exclude keywords: {criteria.get('keywords_exclude') or 'none'}
Exclude industries: {criteria.get('industries_exclude') or 'none'}"""


def _job_block(job: dict) -> str:
    return f"""Title: {job.get('title')}
Company: {job.get('company')}
Location: {job.get('location')}
Remote: {job.get('remote_type')}
Salary: {job.get('salary_min')} to {job.get('salary_max')}
Description: {(job.get('description') or '')[:800]}"""


def score_job(job: dict, criteria: dict,
              credentials: Credentials | None = None) -> tuple[int, str]:
    """Return (score, reason) for one job. Never raises — scoring is best-effort."""
    prompt = (
        f"CANDIDATE CRITERIA:\n{_criteria_block(criteria)}\n\n"
        f"JOB POSTING:\n{_job_block(job)}"
    )
    try:
        # Low effort: this is a bounded classification, not open-ended reasoning.
        result = complete_json(
            system=SYSTEM, prompt=prompt, schema=JobScore, max_tokens=2000, effort="low",
            credentials=credentials,
        )
    except LLMError as exc:
        print(f"[scorer] {job.get('title')!r}: {exc}")
        return 0, f"Could not score automatically ({exc})."
    return result.score, result.reason


def score_jobs_for_user(all_jobs: list[dict], user_id: int, criteria: dict,
                        credentials: Credentials | None = None
                        ) -> tuple[list[tuple[dict, int, str]], dict]:
    """Score the most relevant postings and keep those at or above the threshold.

    Only the top MAX_TO_SCORE candidates (by cheap local relevance) are sent to
    the model, so a free, rate-limited key isn't asked to score thousands of
    postings and throttled into matching nothing.

    Returns (kept, stats). `stats` explains a thin result: how many postings
    cleared the relevance prefilter, how many the model actually scored, the
    best score it gave, and how many errored (a rate-limited key shows up here).
    """
    candidates = prefilter_jobs(all_jobs, criteria, MAX_TO_SCORE)
    kept, best, errors = [], 0, 0
    for job in candidates:
        score, reason = score_job(job, criteria, credentials)
        if reason.startswith("Could not score"):
            errors += 1
            continue
        best = max(best, score)
        if score >= MATCH_THRESHOLD:
            kept.append((job, score, reason))
    stats = {
        "candidates": len(candidates),
        "scored": len(candidates) - errors,
        "errored": errors,
        "best": best,
        "threshold": MATCH_THRESHOLD,
    }
    return kept, stats


def explain_no_matches(stats: dict) -> str:
    """A plain sentence for a digest that found nothing to send."""
    if stats.get("candidates", 0) == 0:
        return ("No postings matched your job titles closely enough to score. "
                "Broaden the titles in Settings, or add a company to watch.")
    if stats.get("scored", 0) == 0 and stats.get("errored", 0):
        return ("Your AI key was rate-limited before it could score anything. "
                "Wait a few minutes and run it again, or use a paid key.")
    best, threshold = stats.get("best", 0), stats.get("threshold", MATCH_THRESHOLD)
    return (f"Scored {stats.get('scored', 0)} postings; the best was {best}%, "
            f"under the {threshold}% bar. Loosen your criteria (titles, salary "
            "floor, locations) to let more through.")
