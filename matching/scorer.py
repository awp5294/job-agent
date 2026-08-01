"""Score jobs against user criteria using Claude."""
import json
import os
import anthropic

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = "claude-sonnet-4-6"


def score_job(job: dict, criteria: dict) -> tuple[int, str]:
    """Returns (score 0-100, one-line reason)."""
    prompt = f"""Score this job posting against the candidate's criteria. Return JSON only.

CANDIDATE CRITERIA:
- Target titles: {criteria.get('job_titles', [])}
- Locations: {criteria.get('locations', [])}
- Remote preference: {criteria.get('remote_preference', 'any')}
- Salary range: ${criteria.get('min_salary', 0)}–${criteria.get('max_salary', 0) or 'open'}
- Seniority: {criteria.get('seniority_levels', [])}
- Must include keywords: {criteria.get('keywords_include', [])}
- Exclude keywords: {criteria.get('keywords_exclude', [])}
- Exclude industries: {criteria.get('industries_exclude', [])}

JOB POSTING:
Title: {job.get('title')}
Company: {job.get('company')}
Location: {job.get('location')}
Remote: {job.get('remote_type')}
Salary: {job.get('salary_min')}–{job.get('salary_max')}
Description snippet: {(job.get('description') or '')[:800]}

Respond with exactly: {{"score": <0-100>, "reason": "<one sentence why this does or doesn't match>"}}
Score 90-100 = excellent match, 70-89 = good match, 50-69 = partial, <50 = poor match.
Only score ≥70 should be included in a digest. Be honest and specific."""

    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        # extract JSON even if wrapped in markdown
        if "```" in text:
            text = text.split("```")[1].replace("json", "").strip()
        data = json.loads(text)
        return int(data["score"]), data["reason"]
    except Exception as e:
        print(f"[scorer] error: {e}")
        return 50, "Could not score automatically."


def score_jobs_for_user(job_ids: list[int], criteria: dict) -> list[tuple[int, int, str]]:
    """Returns list of (job_id, score, reason) for jobs scoring ≥70."""
    from db.database import get_conn
    conn = get_conn()
    results = []
    for job_id in job_ids:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            continue
        job = dict(row)
        score, reason = score_job(job, criteria)
        if score >= 70:
            results.append((job_id, score, reason))
    conn.close()
    return results
