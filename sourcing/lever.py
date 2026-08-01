"""Lever public API sourcer — no auth required."""
import httpx
import asyncio
from db.database import upsert_job

BASE = "https://api.lever.co/v0/postings/{slug}?mode=json"


async def fetch_lever(company_slug: str) -> list[dict]:
    url = BASE.format(slug=company_slug)
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[lever] {company_slug}: {e}")
            return []


def parse_job(job: dict, company_slug: str) -> dict:
    categories = job.get("categories", {})
    location = categories.get("location", "")
    commitment = categories.get("commitment", "")
    return {
        "source": "lever",
        "external_id": job.get("id", ""),
        "title": job.get("text", ""),
        "company": company_slug.replace("-", " ").title(),
        "apply_url": job.get("hostedUrl", ""),
        "location": location,
        "description": job.get("descriptionPlain", "")[:3000],
        "remote_type": "remote" if "remote" in location.lower() else None,
        "salary_min": None,
        "salary_max": None,
    }


async def source_lever(company_slugs: list[str]) -> list[int]:
    job_ids = []
    for slug in company_slugs:
        jobs = await fetch_lever(slug)
        for job in jobs:
            parsed = parse_job(job, slug)
            if parsed["apply_url"]:
                job_id = upsert_job(**parsed)
                job_ids.append(job_id)
        await asyncio.sleep(0.5)
    return job_ids
