"""Greenhouse public API sourcer — no auth required."""
import httpx
import asyncio
from db.database import upsert_job

BASE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


async def fetch_greenhouse(company_slug: str) -> list[dict]:
    url = BASE.format(slug=company_slug)
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url, params={"content": "true"})
            r.raise_for_status()
            data = r.json()
            return data.get("jobs", [])
        except Exception as e:
            print(f"[greenhouse] {company_slug}: {e}")
            return []


def parse_job(job: dict, company_slug: str) -> dict:
    loc = job.get("location", {}).get("name", "")
    salary_min = salary_max = None
    # Greenhouse sometimes embeds salary in the title or metadata
    return {
        "source": "greenhouse",
        "external_id": str(job["id"]),
        "title": job.get("title", ""),
        "company": company_slug.replace("-", " ").title(),
        "apply_url": job.get("absolute_url", ""),
        "location": loc,
        "description": job.get("content", "")[:3000],
        "salary_min": salary_min,
        "salary_max": salary_max,
        "remote_type": "remote" if "remote" in loc.lower() else None,
    }


async def source_greenhouse(company_slugs: list[str]) -> list[int]:
    """Returns list of job IDs stored in DB."""
    job_ids = []
    for slug in company_slugs:
        jobs = await fetch_greenhouse(slug)
        for job in jobs:
            parsed = parse_job(job, slug)
            job_id = upsert_job(**parsed)
            job_ids.append(job_id)
        await asyncio.sleep(0.5)
    return job_ids
