"""The daily digest: sourcing, scoring, emailing, and reading replies back."""
import asyncio

import pytest

import server
from db import database
from email_handler.gmail import (
    _build_digest_text, build_digest_message, extract_reply_numbers,
)

GREENHOUSE_JOBS = [
    {
        "source": "greenhouse", "external_id": "gh-1", "title": "Staff Product Manager",
        "company": "Stripe", "apply_url": "https://boards.greenhouse.io/stripe/jobs/1",
        "location": "Remote", "description": "Own payments platform strategy.",
        "salary_min": None, "salary_max": None, "remote_type": "remote",
    },
    {
        "source": "greenhouse", "external_id": "gh-2", "title": "Warehouse Associate",
        "company": "Stripe", "apply_url": "https://boards.greenhouse.io/stripe/jobs/2",
        "location": "Dublin", "description": "Move boxes.",
        "salary_min": None, "salary_max": None, "remote_type": None,
    },
]

LEVER_JOBS = [
    {
        "source": "lever", "external_id": "lv-1", "title": "Senior PM, Growth",
        "company": "Figma", "apply_url": "https://jobs.lever.co/figma/1",
        "location": "Remote", "description": "Own growth surface area.",
        "salary_min": None, "salary_max": None, "remote_type": "remote",
    },
]


@pytest.fixture
def stub_sources(monkeypatch):
    """Replace the network with fixed postings; score on title keywords."""
    monkeypatch.setattr(server, "fetch_greenhouse_jobs", lambda slug: list(GREENHOUSE_JOBS))
    monkeypatch.setattr(server, "fetch_lever_jobs", lambda slug: list(LEVER_JOBS))
    monkeypatch.setattr(server, "fetch_indeed_jobs", lambda titles, locations: [])

    def fake_score(all_jobs, user_id, criteria):
        results = []
        for job in all_jobs:
            if "Product Manager" in job["title"] or "PM" in job["title"]:
                results.append((job, 91, "Title matches a target role."))
        return results

    monkeypatch.setattr(server, "score_jobs_for_user", fake_score)


@pytest.fixture
def sent_emails(monkeypatch):
    sent = []

    def fake_send(user, jobs):
        sent.append({"to": user["email"], "jobs": list(jobs)})
        return True, ""

    monkeypatch.setattr(server, "_send_digest", fake_send)
    return sent


def configure_sources(user_id):
    database.update_criteria(user_id, {
        "greenhouse_companies": ["stripe"],
        "lever_companies": ["figma"],
        "job_titles": ["Product Manager"],
    })


def test_digest_sources_scores_stores_and_emails(signed_up, stub_sources, sent_emails):
    user = database.get_user_by_email("ada@example.com")
    configure_sources(user["id"])

    result = asyncio.run(server.run_digest_for_user(user["id"]))
    assert result["status"] == "ok"

    # Three postings were fetched; the warehouse role scored out.
    jobs = database.get_user_jobs(user["id"])
    titles = sorted(j["title"] for j in jobs)
    assert titles == ["Senior PM, Growth", "Staff Product Manager"]
    assert all(j["score"] == 91 for j in jobs)

    # The email went out with both matches, numbered.
    assert len(sent_emails) == 1
    assert sent_emails[0]["to"] == "ada@example.com"
    assert len(sent_emails[0]["jobs"]) == 2

    run = database.get_latest_digest_run(user["id"])
    assert run["status"] == "ok"
    assert run["jobs_found"] == 3
    assert run["jobs_matched"] == 2
    assert run["email_sent"] == 1


def test_digest_status_endpoint_reports_the_run(signed_up, stub_sources, sent_emails):
    user = database.get_user_by_email("ada@example.com")
    configure_sources(user["id"])
    asyncio.run(server.run_digest_for_user(user["id"]))

    status = signed_up.get("/api/digest-status").json()
    assert status["status"] == "ok"
    assert status["jobs_found"] == 3
    assert status["jobs_matched"] == 2
    assert status["email_sent"] is True


def test_digest_records_source_failures_instead_of_swallowing_them(signed_up, monkeypatch):
    user = database.get_user_by_email("ada@example.com")
    configure_sources(user["id"])

    def broken(slug):
        raise RuntimeError("503 from job board")

    monkeypatch.setattr(server, "fetch_greenhouse_jobs", broken)
    monkeypatch.setattr(server, "fetch_lever_jobs", lambda slug: [])
    monkeypatch.setattr(server, "fetch_indeed_jobs", lambda t, l: [])
    monkeypatch.setattr(server, "score_jobs_for_user", lambda j, u, c: [])

    asyncio.run(server.run_digest_for_user(user["id"]))
    run = database.get_latest_digest_run(user["id"])
    assert run["status"] == "ok"
    assert "greenhouse/stripe: 503 from job board" in run["message"]


def test_digest_without_email_configured_still_saves_matches(signed_up, stub_sources,
                                                             monkeypatch):
    """No Gmail, no SMTP: the run should succeed and say why nothing was sent."""
    monkeypatch.setattr("server.smtp_configured", lambda: False)
    user = database.get_user_by_email("ada@example.com")
    configure_sources(user["id"])

    asyncio.run(server.run_digest_for_user(user["id"]))

    assert len(database.get_user_jobs(user["id"])) == 2
    run = database.get_latest_digest_run(user["id"])
    assert run["email_sent"] == 0
    assert "email not configured" in run["message"]
    # Nothing was emailed, so the jobs stay 'new' and go out next time.
    assert all(j["status"] == "new" for j in database.get_user_jobs(user["id"]))


def test_rerunning_the_digest_does_not_duplicate_jobs(signed_up, stub_sources, sent_emails):
    user = database.get_user_by_email("ada@example.com")
    configure_sources(user["id"])

    asyncio.run(server.run_digest_for_user(user["id"]))
    asyncio.run(server.run_digest_for_user(user["id"]))

    assert len(database.get_user_jobs(user["id"])) == 2
    # Second run had nothing new to send.
    assert len(sent_emails) == 1


def test_digest_run_is_scoped_to_one_user(signed_up, browser, stub_sources, sent_emails):
    from tests.conftest import walk_onboarding

    ada = database.get_user_by_email("ada@example.com")
    grace_browser = browser()
    grace_browser.get(f"/onboard?invite={ada['invite_token']}")
    walk_onboarding(grace_browser, email="grace@example.com")
    grace_browser.post("/api/finish-signup")
    grace = database.get_user_by_email("grace@example.com")

    configure_sources(ada["id"])
    asyncio.run(server.run_digest_for_user(ada["id"]))

    assert len(database.get_user_jobs(ada["id"])) == 2
    assert database.get_user_jobs(grace["id"]) == []
    assert database.get_latest_digest_run(grace["id"]) is None


# ── Replies ────────────────────────────────────────────────────────────────

def test_digest_positions_are_recorded_when_sent(signed_up, stub_sources, sent_emails):
    user = database.get_user_by_email("ada@example.com")
    configure_sources(user["id"])
    asyncio.run(server.run_digest_for_user(user["id"]))

    batch = database.get_latest_digest_batch(user["id"])
    assert batch is not None
    numbered = database.get_digest_batch_jobs(user["id"], batch)
    assert [j["digest_position"] for j in numbered] == [1, 2]
    assert all(j["status"] == "sent" for j in numbered)


def test_replying_with_numbers_selects_those_jobs(signed_up, stub_sources, sent_emails,
                                                  monkeypatch):
    """Regression: reply numbers were matched against jobs already marked sent,
    so a reply never selected anything."""
    user = database.get_user_by_email("ada@example.com")
    configure_sources(user["id"])
    asyncio.run(server.run_digest_for_user(user["id"]))

    database.update_user(user["id"], {"gmail_credentials": '{"token": "fake"}'})
    monkeypatch.setattr(server, "get_gmail_service", lambda creds: object())
    monkeypatch.setattr(server, "poll_for_replies", lambda service, email, n: [2])

    response = signed_up.post("/api/poll-replies")
    assert response.status_code == 200
    assert len(response.json()["selected"]) == 1

    batch = database.get_latest_digest_batch(user["id"])
    by_position = {j["digest_position"]: j for j in
                   database.get_digest_batch_jobs(user["id"], batch)}
    assert by_position[2]["status"] == "selected"
    assert by_position[1]["status"] == "sent"


def test_polling_twice_does_not_undo_later_work(signed_up, stub_sources, sent_emails,
                                                monkeypatch):
    user = database.get_user_by_email("ada@example.com")
    configure_sources(user["id"])
    asyncio.run(server.run_digest_for_user(user["id"]))

    database.update_user(user["id"], {"gmail_credentials": '{"token": "fake"}'})
    monkeypatch.setattr(server, "get_gmail_service", lambda creds: object())
    monkeypatch.setattr(server, "poll_for_replies", lambda service, email, n: [1])

    signed_up.post("/api/poll-replies")
    batch = database.get_latest_digest_batch(user["id"])
    uj = database.get_digest_batch_jobs(user["id"], batch)[0]
    database.update_user_job(uj["id"], {"status": "applied"})

    signed_up.post("/api/poll-replies")
    assert database.get_user_job(uj["id"])["status"] == "applied"


def test_reply_parsing_ignores_the_quoted_digest():
    reply = """1, 3

On Tue, Ada wrote:
> 1. Staff Product Manager @ Stripe — 91% match
> 2. Senior PM, Growth @ Figma — 91% match
> 4. Something else
"""
    assert extract_reply_numbers(reply, max_number=4) == [1, 3]


def test_reply_parsing_bounds_numbers_to_the_digest():
    assert extract_reply_numbers("2 and 99 please", max_number=3) == [2]
    assert extract_reply_numbers("none of them thanks", max_number=3) == []


def test_digest_email_numbers_jobs_in_order():
    jobs = [
        {"title": "Staff PM", "company": "Stripe", "location": "Remote",
         "score": 91, "score_reason": "Good fit.", "apply_url": "https://x/1"},
        {"title": "Senior PM", "company": "Figma", "location": "Remote",
         "score": 88, "score_reason": "Also good.", "apply_url": "https://x/2"},
    ]
    text = _build_digest_text("Ada", jobs)
    assert "1. Staff PM @ Stripe" in text
    assert "2. Senior PM @ Figma" in text

    message = build_digest_message("ada@example.com", "Ada", jobs)
    assert "2 matches" in message["Subject"]
    assert message["To"] == "ada@example.com"
    assert {part.get_content_type() for part in message.get_payload()} == {
        "text/plain", "text/html"
    }
