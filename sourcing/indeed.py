"""Indeed scraper.

Indeed actively blocks scrapers, so treat an empty result as normal rather than
as a bug — the digest reports it as a note. Greenhouse and Lever are the
reliable sources; this one is a bonus.
"""
import hashlib
import time

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

MAX_TITLES = 3
MAX_LOCATIONS = 2
MAX_CARDS = 10


def _make_id(title: str, company: str, url: str) -> str:
    return hashlib.md5(f"{title}{company}{url}".encode()).hexdigest()[:16]


def _parse_card(card, fallback_location: str) -> dict | None:
    title_el = card.select_one("h2.jobTitle span")
    link_el = card.select_one("h2.jobTitle a")
    if not title_el or not link_el:
        return None

    company_el = card.select_one("[data-testid='company-name']")
    location_el = card.select_one("[data-testid='text-location']")

    title = title_el.get_text(strip=True)
    company = company_el.get_text(strip=True) if company_el else "Unknown"
    location = location_el.get_text(strip=True) if location_el else fallback_location
    href = link_el.get("href", "")
    apply_url = f"https://www.indeed.com{href}" if href.startswith("/") else href
    if not apply_url:
        return None

    return {
        "source": "indeed",
        "external_id": _make_id(title, company, apply_url),
        "title": title,
        "company": company,
        "apply_url": apply_url,
        "location": location,
        "description": "",
        "salary_min": None,
        "salary_max": None,
        "remote_type": "remote" if "remote" in location.lower() else None,
        "company_domain": "",
    }


def fetch_indeed_jobs(titles: list[str], locations: list[str]) -> list[dict]:
    results: list[dict] = []
    searches = [
        (t, l)
        for t in titles[:MAX_TITLES]
        for l in (locations or ["Remote"])[:MAX_LOCATIONS]
    ]

    with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        for index, (title, location) in enumerate(searches):
            try:
                response = client.get(
                    "https://www.indeed.com/jobs", params={"q": title, "l": location}
                )
                soup = BeautifulSoup(response.text, "html.parser")
                for card in soup.select("div.job_seen_beacon")[:MAX_CARDS]:
                    parsed = _parse_card(card, location)
                    if parsed:
                        results.append(parsed)
            except Exception as exc:
                print(f"[indeed] {title} / {location}: {exc}")
            if index < len(searches) - 1:
                time.sleep(2)  # be polite between searches

    return results
