"""Greenhouse public job-board API. No auth required.

Sourcers only fetch and normalise — the server decides what to store.
"""
import os

import httpx

# Overridable so you can point at a proxy or a stand-in during testing.
BASE = os.getenv("GREENHOUSE_API_BASE",
                 "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")


def parse_job(job: dict, company_slug: str) -> dict:
    location = (job.get("location") or {}).get("name", "")
    return {
        "source": "greenhouse",
        "external_id": str(job.get("id", "")),
        "title": job.get("title", ""),
        "company": company_slug.replace("-", " ").title(),
        "apply_url": job.get("absolute_url", ""),
        "location": location,
        "description": (job.get("content") or "")[:3000],
        "salary_min": None,
        "salary_max": None,
        "remote_type": "remote" if "remote" in location.lower() else None,
    }


def fetch_greenhouse_jobs(company_slug: str) -> list[dict]:
    """Return normalised job dicts for one company board."""
    with httpx.Client(timeout=15) as client:
        response = client.get(BASE.format(slug=company_slug), params={"content": "true"})
        response.raise_for_status()
        jobs = response.json().get("jobs", [])
    return [parse_job(j, company_slug) for j in jobs if j.get("absolute_url")]
