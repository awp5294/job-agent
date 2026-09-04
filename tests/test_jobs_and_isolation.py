"""Per-user job data: the dashboard API and cross-account isolation."""
import pytest

import server
from db import database
from tests.conftest import walk_onboarding


def make_job(user_id, title="Staff PM", company="Acme", score=88, status="new",
             external_id=None):
    job_id = database.upsert_job(
        source="greenhouse",
        external_id=external_id or f"{company}-{title}".replace(" ", "-"),
        title=title,
        company=company,
        apply_url=f"https://boards.greenhouse.io/{company.lower()}/jobs/1",
        location="Remote",
        description="Own the roadmap for the platform team.",
    )
    database.upsert_user_job(user_id, job_id, score, "Title and remote policy both match.")
    uj = [u for u in database.get_user_jobs(user_id) if u["job_id"] == job_id][0]
    if status != "new":
        database.update_user_job(uj["id"], {"status": status})
    return uj["id"]


@pytest.fixture
def two_users(signed_up, browser):
    """Ada (signed_up, the owner) plus Grace, who joined via Ada's invite."""
    ada = database.get_user_by_email("ada@example.com")

    grace_browser = browser()
    grace_browser.get(f"/onboard?invite={ada['invite_token']}")
    walk_onboarding(grace_browser, email="grace@example.com")
    grace = database.get_user_by_email("grace@example.com")

    return {
        "ada": ada, "ada_browser": signed_up,
        "grace": grace, "grace_browser": grace_browser,
    }


def test_each_user_only_sees_their_own_jobs(two_users):
    make_job(two_users["ada"]["id"], title="Ada's Job", company="Alpha")
    make_job(two_users["grace"]["id"], title="Grace's Job", company="Beta")

    ada_jobs = two_users["ada_browser"].get("/api/jobs").json()
    grace_jobs = two_users["grace_browser"].get("/api/jobs").json()

    assert [j["title"] for j in ada_jobs] == ["Ada's Job"]
    assert [j["title"] for j in grace_jobs] == ["Grace's Job"]


@pytest.mark.parametrize("path,payload", [
    ("ignore", None),
    ("update-cover-letter", {"cover_letter": "pwned"}),
    ("mark-applied", {"cover_letter": "pwned"}),
    ("select", None),
])
def test_one_user_cannot_touch_another_users_job(two_users, path, payload, monkeypatch):
    """Regression: these endpoints used to update by id with no ownership check."""
    monkeypatch.setattr(server, "generate_cover_letter", lambda **kw: "letter")
    ada_job = make_job(two_users["ada"]["id"])

    response = two_users["grace_browser"].post(
        f"/api/jobs/{ada_job}/{path}",
        json=payload if payload is not None else None,
    )
    assert response.status_code == 404

    unchanged = database.get_user_job(ada_job)
    assert unchanged["status"] == "new"
    assert unchanged["cover_letter_text"] is None


def test_signed_out_users_get_401_not_data(browser, signed_up):
    ada_job = make_job(database.get_user_by_email("ada@example.com")["id"])
    stranger = browser()
    assert stranger.get("/api/jobs").status_code == 401
    assert stranger.post(f"/api/jobs/{ada_job}/ignore").status_code == 401
    assert stranger.post("/api/run-digest").status_code == 401
    assert stranger.get("/api/digest-status").status_code == 401


def test_apply_flow_generates_and_stores_a_cover_letter(signed_up, monkeypatch):
    user = database.get_user_by_email("ada@example.com")
    uj_id = make_job(user["id"])

    calls = {}

    def fake_letter(**kwargs):
        calls.update(kwargs)
        return "I built the analytical engine's developer platform."

    monkeypatch.setattr(server, "generate_cover_letter", fake_letter)

    response = signed_up.post(f"/api/jobs/{uj_id}/select")
    assert response.status_code == 200
    body = response.json()
    assert body["cover_letter"].startswith("I built")
    assert body["apply_url"].startswith("https://boards.greenhouse.io/")

    # The generator got this user's real resume and job details.
    assert calls["job_title"] == "Staff PM"
    assert "analytical engines" in calls["resume_text"]

    stored = database.get_user_job(uj_id)
    assert stored["status"] == "selected"
    assert stored["cover_letter_text"].startswith("I built")

    # Selecting again reuses the stored letter rather than paying for a new one.
    calls.clear()
    signed_up.post(f"/api/jobs/{uj_id}/select")
    assert calls == {}


def test_apply_reports_llm_failure_instead_of_silently_succeeding(signed_up, monkeypatch):
    from llm import LLMError

    user = database.get_user_by_email("ada@example.com")
    uj_id = make_job(user["id"])

    def boom(**kwargs):
        raise LLMError("Anthropic rejected the API key.")

    monkeypatch.setattr(server, "generate_cover_letter", boom)
    response = signed_up.post(f"/api/jobs/{uj_id}/select")
    assert response.status_code == 502
    assert "API key" in response.json()["detail"]

    # The job stays in New so it can be retried, rather than landing in
    # Selected with an empty cover letter.
    assert database.get_user_job(uj_id)["status"] == "new"


def test_ignore_and_mark_applied_move_a_job(signed_up):
    user = database.get_user_by_email("ada@example.com")
    ignored = make_job(user["id"], title="No thanks", external_id="a")
    applied = make_job(user["id"], title="Yes please", external_id="b")

    signed_up.post(f"/api/jobs/{ignored}/ignore")
    signed_up.post(f"/api/jobs/{applied}/mark-applied", json={"cover_letter": "final text"})

    assert database.get_user_job(ignored)["status"] == "ignored"
    applied_row = database.get_user_job(applied)
    assert applied_row["status"] == "applied"
    assert applied_row["cover_letter_text"] == "final text"
    assert applied_row["applied_at"] is not None
