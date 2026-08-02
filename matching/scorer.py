"""Score jobs against a user's criteria with Claude."""
from pydantic import BaseModel, Field

from llm import LLMError, complete_json

# Only jobs at or above this score make it into a digest.
MATCH_THRESHOLD = 70

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


def score_job(job: dict, criteria: dict) -> tuple[int, str]:
    """Return (score, reason) for one job. Never raises — scoring is best-effort."""
    prompt = (
        f"CANDIDATE CRITERIA:\n{_criteria_block(criteria)}\n\n"
        f"JOB POSTING:\n{_job_block(job)}"
    )
    try:
        # Low effort: this is a bounded classification, not open-ended reasoning.
        result = complete_json(
            system=SYSTEM, prompt=prompt, schema=JobScore, max_tokens=2000, effort="low"
        )
    except LLMError as exc:
        print(f"[scorer] {job.get('title')!r}: {exc}")
        return 0, f"Could not score automatically ({exc})."
    return result.score, result.reason


def score_jobs_for_user(all_jobs: list[dict], user_id: int,
                        criteria: dict) -> list[tuple[dict, int, str]]:
    """Score every stored job and keep the ones at or above the match threshold."""
    results = []
    for job in all_jobs:
        if not job.get("id"):
            continue
        score, reason = score_job(job, criteria)
        if score >= MATCH_THRESHOLD:
            results.append((job, score, reason))
    return results
