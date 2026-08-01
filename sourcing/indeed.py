"""Indeed scraper using httpx + BeautifulSoup."""
import httpx
import hashlib
from bs4 import BeautifulSoup
from db.database import upsert_job

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _make_id(title: str, company: str, url: str) -> str:
    return hashlib.md5(f"{title}{company}{url}".encode()).hexdigest()[:16]


async def source_indeed(titles: list[str], locations: list[str]) -> list[int]:
    import asyncio
    job_ids = []
    async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        for title in titles[:3]:  # limit to avoid rate limits
            for location in (locations or ["Remote"])[:2]:
                url = "https://www.indeed.com/jobs"
                params = {"q": title, "l": location, "remotejob": "032b3046-06a3-4876-8dfd-474eb5e7ed11"}
                try:
                    r = await client.get(url, params=params)
                    soup = BeautifulSoup(r.text, "html.parser")
                    cards = soup.select("div.job_seen_beacon")
                    for card in cards[:10]:
                        title_el = card.select_one("h2.jobTitle span")
                        company_el = card.select_one("[data-testid='company-name']")
                        loc_el = card.select_one("[data-testid='text-location']")
                        link_el = card.select_one("h2.jobTitle a")

                        if not title_el or not link_el:
                            continue

                        job_title = title_el.get_text(strip=True)
                        company = company_el.get_text(strip=True) if company_el else "Unknown"
                        job_location = loc_el.get_text(strip=True) if loc_el else location
                        href = link_el.get("href", "")
                        apply_url = f"https://www.indeed.com{href}" if href.startswith("/") else href
                        ext_id = _make_id(job_title, company, apply_url)

                        job_id = upsert_job(
                            source="indeed",
                            external_id=ext_id,
                            title=job_title,
                            company=company,
                            apply_url=apply_url,
                            location=job_location,
                            remote_type="remote" if "remote" in job_location.lower() else None,
                        )
                        job_ids.append(job_id)
                except Exception as e:
                    print(f"[indeed] {title} / {location}: {e}")
                await asyncio.sleep(2)
    return job_ids
