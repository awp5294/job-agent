"""Lever public postings API. No auth required."""
import os

import httpx

BASE = os.getenv("LEVER_API_BASE", "https://api.lever.co/v0/postings/{slug}?mode=json")


def parse_job(job: dict, company_slug: str) -> dict:
    categories = job.get("categories") or {}
    location = categories.get("location") or ""
    return {
        "source": "lever",
        "external_id": job.get("id", ""),
        "title": job.get("text", ""),
        "company": company_slug.replace("-", " ").title(),
        "apply_url": job.get("hostedUrl", ""),
        "location": location,
        "description": (job.get("descriptionPlain") or "")[:3000],
        "salary_min": None,
        "salary_max": None,
        "remote_type": "remote" if "remote" in location.lower() else None,
    }


def fetch_lever_jobs(company_slug: str) -> list[dict]:
    with httpx.Client(timeout=15) as client:
        response = client.get(BASE.format(slug=company_slug))
        response.raise_for_status()
        postings = response.json()
    return [parse_job(j, company_slug) for j in postings if j.get("hostedUrl")]
