"""Remotive: a public job API you can search by keyword.

Greenhouse and Lever only answer "what is company X hiring for?", so they need
a company list. This one answers "who is hiring a Product Manager?" across
thousands of companies at once, which is what someone actually wants from
onboarding alone. Remote roles only.

Public API, no key, no scraping: https://remotive.com/api/remote-jobs
"""
import os

import httpx

BASE = os.getenv("REMOTIVE_API_BASE", "https://remotive.com/api/remote-jobs")
MAX_TITLES = 3
PER_TITLE = 25


def parse_job(job: dict) -> dict:
    salary_min, salary_max = _parse_salary(job.get("salary") or "")
    return {
        "source": "remotive",
        "external_id": str(job.get("id", "")),
        "title": job.get("title", ""),
        "company": job.get("company_name", ""),
        "apply_url": job.get("url", ""),
        "location": job.get("candidate_required_location") or "Remote",
        "description": (job.get("description") or "")[:3000],
        "salary_min": salary_min,
        "salary_max": salary_max,
        "remote_type": "remote",
    }


def _parse_salary(raw: str) -> tuple[int | None, int | None]:
    """Remotive salary is free text like '$120,000 - $160,000' or ''."""
    import re

    numbers = [
        int(n.replace(",", "").replace("k", "000").replace("K", "000"))
        for n in re.findall(r"\d[\d,]*[kK]?", raw)
    ]
    # Anything under 1000 is a percentage, an hourly rate, or a year — not a salary.
    numbers = [n for n in numbers if 1000 <= n <= 10_000_000]
    if len(numbers) >= 2:
        return min(numbers[:2]), max(numbers[:2])
    if len(numbers) == 1:
        return numbers[0], None
    return None, None


def fetch_remotive_jobs(titles: list[str]) -> list[dict]:
    """Search by job title. Returns normalised job dicts, deduplicated."""
    seen: set[str] = set()
    results: list[dict] = []

    with httpx.Client(timeout=20, follow_redirects=True) as client:
        for title in titles[:MAX_TITLES]:
            response = client.get(BASE, params={"search": title, "limit": PER_TITLE})
            response.raise_for_status()
            for raw in response.json().get("jobs", []):
                parsed = parse_job(raw)
                # The same posting comes back under several search terms.
                if not parsed["apply_url"] or parsed["external_id"] in seen:
                    continue
                seen.add(parsed["external_id"])
                results.append(parsed)

    return results
