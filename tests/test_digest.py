"""The daily digest: sourcing, scoring, emailing, and reading replies back."""
import asyncio

import pytest

import server
from db import database
from email_handler.digest import build_digest_text, digest_subject
from email_handler.mailbox import build_message, extract_reply_numbers

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
    monkeypatch.setattr(server, "fetch_remotive_jobs", lambda titles: [])

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


def test_a_new_account_can_find_jobs_without_configuring_anything(signed_up):
    """A friend who just finishes onboarding must have somewhere to search."""
    user = database.get_user_by_email("ada@example.com")
    criteria = database.get_criteria(user["id"])
    assert criteria["greenhouse_companies"] == server.DEFAULT_GREENHOUSE_COMPANIES
    assert criteria["lever_companies"] == server.DEFAULT_LEVER_COMPANIES


def test_digest_sends_ten_jobs_and_saves_the_rest_for_next_time(signed_up, sent_emails,
                                                                monkeypatch):
    many = [
        {
            "source": "greenhouse", "external_id": f"gh-{i}",
            "title": f"Product Manager {i}", "company": "Acme",
            "apply_url": f"https://boards.greenhouse.io/acme/jobs/{i}",
            "location": "Remote", "description": "Build things.",
            "salary_min": None, "salary_max": None, "remote_type": "remote",
        }
        for i in range(25)
    ]
    monkeypatch.setattr(server, "fetch_greenhouse_jobs", lambda slug: list(many))
    monkeypatch.setattr(server, "fetch_lever_jobs", lambda slug: [])
    monkeypatch.setattr(server, "fetch_indeed_jobs", lambda t, l: [])
    monkeypatch.setattr(server, "fetch_remotive_jobs", lambda titles: [])
    # Descending scores, so the cut is by quality rather than arbitrary.
    monkeypatch.setattr(
        server, "score_jobs_for_user",
        lambda jobs, uid, crit: [(j, 99 - i, "match") for i, j in enumerate(jobs)],
    )

    user = database.get_user_by_email("ada@example.com")
    database.update_criteria(user["id"], {"greenhouse_companies": ["acme"],
                                          "lever_companies": []})
    asyncio.run(server.run_digest_for_user(user["id"]))

    assert len(sent_emails[0]["jobs"]) == server.DIGEST_LIMIT == 10
    # The ten best went out; the other fifteen are queued for the next digest.
    assert [j["score"] for j in sent_emails[0]["jobs"]] == list(range(99, 89, -1))
    assert len(database.get_user_jobs(user["id"], status="sent")) == 10
    assert len(database.get_user_jobs(user["id"], status="new")) == 15

    asyncio.run(server.run_digest_for_user(user["id"]))
    assert len(sent_emails[1]["jobs"]) == 10
    assert len(database.get_user_jobs(user["id"], status="new")) == 5


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
    monkeypatch.setattr(server, "fetch_remotive_jobs", lambda titles: [])
    monkeypatch.setattr(server, "score_jobs_for_user", lambda j, u, c: [])

    asyncio.run(server.run_digest_for_user(user["id"]))
    run = database.get_latest_digest_run(user["id"])
    assert run["status"] == "ok"
    assert "greenhouse/stripe: 503 from job board" in run["message"]


def test_digest_without_email_configured_still_saves_matches(signed_up, stub_sources,
                                                             monkeypatch):
    """No mailbox: the run should succeed and say why nothing was sent."""
    monkeypatch.setattr(server, "mailbox_configured", lambda: False)
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


@pytest.fixture
def stub_replies(monkeypatch):
    """Stub the shared inbox, the cover-letter writer, and outgoing mail."""
    state = {"inbox": [], "sent": []}

    monkeypatch.setattr(server, "mailbox_configured", lambda: True)
    monkeypatch.setattr(server, "fetch_replies", lambda: list(state["inbox"]))
    monkeypatch.setattr(server, "generate_cover_letter",
                        lambda **kw: f"Cover letter for {kw['job_title']}.")

    def fake_send(to_email, subject, text, html):
        state["sent"].append({"to": to_email, "subject": subject, "text": text})

    monkeypatch.setattr(server, "send_email", fake_send)
    return state


def reply_from(email: str, body: str) -> dict:
    return {"from_email": email, "subject": "Re: Your job digest", "body": body}


def test_replying_with_numbers_selects_those_jobs(signed_up, stub_sources, sent_emails,
                                                  stub_replies):
    """Regression: reply numbers were matched against jobs already marked sent,
    so a reply never selected anything."""
    user = database.get_user_by_email("ada@example.com")
    configure_sources(user["id"])
    asyncio.run(server.run_digest_for_user(user["id"]))

    stub_replies["inbox"] = [reply_from("ada@example.com", "2")]
    result = asyncio.run(server.poll_all_replies())
    assert result["selected"] == 1

    batch = database.get_latest_digest_batch(user["id"])
    by_position = {j["digest_position"]: j for j in
                   database.get_digest_batch_jobs(user["id"], batch)}
    assert by_position[2]["status"] == "selected"
    assert by_position[1]["status"] == "sent"


def test_reply_writes_the_cover_letter_and_emails_it_back(signed_up, stub_sources,
                                                          sent_emails, stub_replies):
    """The whole point: reply from your phone and the work is done for you."""
    user = database.get_user_by_email("ada@example.com")
    configure_sources(user["id"])
    asyncio.run(server.run_digest_for_user(user["id"]))

    stub_replies["inbox"] = [reply_from("ada@example.com", "1, 2")]
    asyncio.run(server.poll_all_replies())

    # Both jobs have a letter stored, without anyone opening the dashboard.
    batch = database.get_latest_digest_batch(user["id"])
    for job in database.get_digest_batch_jobs(user["id"], batch):
        stored = database.get_user_job(job["id"])
        assert stored["status"] == "selected"
        assert stored["cover_letter_text"].startswith("Cover letter for")

    assert len(stub_replies["sent"]) == 1
    email = stub_replies["sent"][0]
    assert email["to"] == "ada@example.com"
    assert "2 cover letters" in email["subject"]
    assert "https://boards.greenhouse.io" in email["text"]


def test_a_reply_is_matched_to_the_person_who_sent_it(signed_up, browser, stub_sources,
                                                      sent_emails, stub_replies):
    """One mailbox serves everyone, so the From address decides whose jobs move."""
    from tests.conftest import walk_onboarding

    ada = database.get_user_by_email("ada@example.com")
    grace_browser = browser()
    grace_browser.get(f"/onboard?invite={ada['invite_token']}")
    walk_onboarding(grace_browser, email="grace@example.com")
    grace = database.get_user_by_email("grace@example.com")

    for user in (ada, grace):
        configure_sources(user["id"])
        asyncio.run(server.run_digest_for_user(user["id"]))

    # Only Grace replies.
    stub_replies["inbox"] = [reply_from("grace@example.com", "1")]
    asyncio.run(server.poll_all_replies())

    grace_batch = database.get_latest_digest_batch(grace["id"])
    grace_jobs = database.get_digest_batch_jobs(grace["id"], grace_batch)
    assert grace_jobs[0]["status"] == "selected"

    ada_batch = database.get_latest_digest_batch(ada["id"])
    assert all(j["status"] == "sent"
               for j in database.get_digest_batch_jobs(ada["id"], ada_batch))
    assert [m["to"] for m in stub_replies["sent"]] == ["grace@example.com"]


def test_mail_from_a_stranger_is_ignored(signed_up, stub_sources, sent_emails,
                                         stub_replies):
    user = database.get_user_by_email("ada@example.com")
    configure_sources(user["id"])
    asyncio.run(server.run_digest_for_user(user["id"]))

    stub_replies["inbox"] = [reply_from("nobody@spam.example", "1, 2, 3")]
    result = asyncio.run(server.poll_all_replies())

    assert result["handled"] == 0
    assert stub_replies["sent"] == []
    batch = database.get_latest_digest_batch(user["id"])
    assert all(j["status"] == "sent"
               for j in database.get_digest_batch_jobs(user["id"], batch))


def test_a_failed_cover_letter_still_sends_the_apply_link(signed_up, stub_sources,
                                                          sent_emails, stub_replies,
                                                          monkeypatch):
    from llm import LLMError

    def boom(**kwargs):
        raise LLMError("The AI provider is down")

    monkeypatch.setattr(server, "generate_cover_letter", boom)

    user = database.get_user_by_email("ada@example.com")
    configure_sources(user["id"])
    asyncio.run(server.run_digest_for_user(user["id"]))

    stub_replies["inbox"] = [reply_from("ada@example.com", "1")]
    asyncio.run(server.poll_all_replies())

    sent = stub_replies["sent"][0]["text"]
    assert "https://" in sent
    assert "The AI provider is down" in sent


def test_no_reply_means_no_email(signed_up, stub_sources, sent_emails, stub_replies):
    user = database.get_user_by_email("ada@example.com")
    configure_sources(user["id"])
    asyncio.run(server.run_digest_for_user(user["id"]))

    stub_replies["inbox"] = []
    asyncio.run(server.poll_all_replies())
    assert stub_replies["sent"] == []


def test_a_reply_with_no_numbers_does_nothing(signed_up, stub_sources, sent_emails,
                                              stub_replies):
    user = database.get_user_by_email("ada@example.com")
    configure_sources(user["id"])
    asyncio.run(server.run_digest_for_user(user["id"]))

    stub_replies["inbox"] = [reply_from("ada@example.com", "thanks, none for me")]
    asyncio.run(server.poll_all_replies())
    assert stub_replies["sent"] == []


def test_polling_without_a_mailbox_is_a_clear_no_op(signed_up, monkeypatch):
    monkeypatch.setattr(server, "mailbox_configured", lambda: False)
    result = asyncio.run(server.poll_all_replies())
    assert result["ok"] is False
    assert "No mailbox" in result["message"]


def test_polling_twice_does_not_undo_later_work(signed_up, stub_sources, sent_emails,
                                                stub_replies):
    user = database.get_user_by_email("ada@example.com")
    configure_sources(user["id"])
    asyncio.run(server.run_digest_for_user(user["id"]))

    stub_replies["inbox"] = [reply_from("ada@example.com", "1")]
    asyncio.run(server.poll_all_replies())

    batch = database.get_latest_digest_batch(user["id"])
    uj = database.get_digest_batch_jobs(user["id"], batch)[0]
    database.update_user_job(uj["id"], {"status": "applied"})

    asyncio.run(server.poll_all_replies())
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
    text = build_digest_text("Ada", jobs)
    assert "1. Staff PM — Stripe" in text
    assert "2. Senior PM — Figma" in text

    message = build_message("ada@example.com", digest_subject(jobs),
                            text, "<html></html>")
    assert "2 matches" in message["Subject"]
    assert message["To"] == "ada@example.com"
    assert {part.get_content_type() for part in message.get_payload()} == {
        "text/plain", "text/html"
    }
