"""Playwright-based form pre-filler for job applications.

OPTIONAL. The web app doesn't use this — the dashboard opens the application in
a new browser tab instead, which is what you want on a hosted deployment where
there is no desktop to open a browser on. This module is here for running the
agent locally against a real browser.

    pip install playwright && playwright install chromium
"""
import asyncio


def _playwright():
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Playwright is not installed. Run: pip install playwright && "
            "playwright install chromium"
        ) from exc
    return async_playwright


async def prefill_application(
    apply_url: str,
    user: dict,
    cover_letter: str,
    headless: bool = False,
):
    """
    Opens the job application URL in a browser, pre-fills common fields,
    and pastes the cover letter. Pauses for user review before submit.
    """
    async with _playwright()() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(apply_url, wait_until="networkidle")
        await asyncio.sleep(2)

        # Common field selectors (Greenhouse, Lever, generic)
        field_map = {
            # Name fields
            '[name="first_name"], [id*="first_name"], [placeholder*="First name" i]': user.get("name", "").split()[0],
            '[name="last_name"], [id*="last_name"], [placeholder*="Last name" i]': " ".join(user.get("name", "").split()[1:]) or "",
            '[name="full_name"], [id*="full_name"], [placeholder*="Full name" i]': user.get("name", ""),
            # Email
            '[name="email"], [id*="email"], [type="email"]': user.get("email", ""),
            # Phone
            '[name="phone"], [id*="phone"], [type="tel"]': user.get("phone", ""),
            # LinkedIn
            '[name*="linkedin" i], [id*="linkedin" i], [placeholder*="linkedin" i]': user.get("linkedin_url", ""),
        }

        for selector, value in field_map.items():
            if not value:
                continue
            try:
                for sel in selector.split(", "):
                    el = page.locator(sel).first
                    if await el.count() > 0:
                        await el.fill(value)
                        break
            except Exception:
                pass

        # Cover letter fields
        cover_selectors = [
            'textarea[name*="cover" i]',
            'textarea[id*="cover" i]',
            'textarea[placeholder*="cover" i]',
            '[data-field-id="cover_letter"] textarea',
            '.cover-letter textarea',
        ]
        for sel in cover_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.fill(cover_letter)
                    break
            except Exception:
                pass

        print(f"\n[apply] Browser open at {apply_url}")
        print("[apply] Fields pre-filled. Review the form and submit when ready.")
        print("[apply] Press Ctrl+C to cancel.\n")

        # Keep browser open for manual review
        try:
            await asyncio.sleep(300)  # 5 min window
        except asyncio.CancelledError:
            pass
        finally:
            await browser.close()


async def open_job_for_review(apply_url: str):
    """Just open the job URL in a visible browser."""
    async with _playwright()() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto(apply_url)
        await asyncio.sleep(300)
        await browser.close()
