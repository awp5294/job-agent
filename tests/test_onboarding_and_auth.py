"""Onboarding, sign-in, and the invite gate."""
import server
from db import database
from tests.conftest import walk_onboarding


def test_onboard_page_actually_renders_its_body(client):
    """Regression: the page used to be served with content-length: 0."""
    response = client.get("/onboard")
    assert response.status_code == 200
    body = response.text
    assert len(body) > 500
    assert "chat-window" in body
    assert 'src="/static/chat.js"' in body
    assert response.headers["content-length"] == str(len(response.content))


def test_onboarding_creates_account_and_signs_in(client):
    final = walk_onboarding(client)
    assert final["action"] == "show_finish"

    response = client.post("/api/finish-signup")
    assert response.json()["ok"] is True
    assert "/auth/token?t=" in response.json()["signin_url"]

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "Ada Lovelace" in dashboard.text

    user = database.get_user_by_email("ada@example.com")
    assert user["is_owner"] == 1
    assert user["resume_text"].startswith("Ada Lovelace.")

    criteria = database.get_criteria(user["id"])
    assert criteria["job_titles"] == ["Product Manager", "Senior PM"]
    assert criteria["locations"] == ["London"]
    assert criteria["min_salary"] == 120000
    assert criteria["max_salary"] == 160000


def test_session_survives_across_requests(signed_up):
    for _ in range(3):
        assert signed_up.get("/dashboard").status_code == 200
    assert signed_up.get("/api/jobs").status_code == 200


def test_root_redirects_by_auth_state(signed_up, browser):
    anonymous = browser().get("/", follow_redirects=False)
    assert anonymous.headers["location"] == "/onboard"

    known = signed_up.get("/", follow_redirects=False)
    assert known.headers["location"] == "/dashboard"


def test_typing_someone_elses_email_does_not_sign_you_in(signed_up, browser):
    """The old flow signed you in as whoever owned the email you typed.

    Uses a holder of a valid invite, so the invite gate isn't what stops them —
    the point is that knowing an email address is not proof of identity.
    """
    owner = database.get_user_by_email("ada@example.com")
    attacker = browser()
    attacker.get(f"/onboard?invite={owner['invite_token']}")
    attacker.post("/api/chat", json={"message": "Mallory"})
    response = attacker.post("/api/chat", json={"message": "ada@example.com"})

    assert response.json()["action"] == "redirect:/signin"
    assert attacker.get("/dashboard", follow_redirects=False).headers["location"] == "/onboard"
    assert attacker.get("/api/jobs").status_code == 401
    # Visiting the sign-in page doesn't hand out a session either.
    attacker.get("/signin")
    assert attacker.get("/api/jobs").status_code == 401


def test_signup_is_blocked_without_an_invite(signed_up, browser):
    stranger = browser()
    stranger.get("/onboard")
    response = stranger.post("/api/chat", json={"message": "Stranger"})
    assert "invite-only" in response.json()["reply"]

    # And the API refuses even if they skip the chat and post directly.
    assert stranger.post("/api/finish-signup").status_code == 403
    assert database.count_users() == 1


def test_invite_link_lets_a_friend_sign_up(signed_up, browser):
    owner = database.get_user_by_email("ada@example.com")

    friend = browser()
    friend.get(f"/onboard?invite={owner['invite_token']}")
    walk_onboarding(friend, email="grace@example.com")
    assert friend.post("/api/finish-signup").json()["ok"] is True

    assert database.count_users() == 2
    grace = database.get_user_by_email("grace@example.com")
    assert grace["is_owner"] == 0
    # Everyone gets their own invite link to pass on.
    assert grace["invite_token"] and grace["invite_token"] != owner["invite_token"]
    assert friend.get("/dashboard").status_code == 200


def test_a_bad_invite_token_is_rejected(signed_up, browser):
    stranger = browser()
    stranger.get("/onboard?invite=not-a-real-token")
    response = stranger.post("/api/chat", json={"message": "Stranger"})
    assert "invite-only" in response.json()["reply"]


def test_invite_gate_can_be_turned_off(signed_up, browser, monkeypatch):
    monkeypatch.setattr(server, "REQUIRE_INVITE", False)
    stranger = browser()
    stranger.get("/onboard")
    walk_onboarding(stranger, email="open@example.com")
    assert stranger.post("/api/finish-signup").json()["ok"] is True


def test_signin_link_works_and_a_forged_one_does_not(signed_up, browser):
    token = database.get_user_by_email("ada@example.com")["login_token"]

    returning = browser()
    assert returning.get(f"/auth/token?t={token}", follow_redirects=False)\
        .headers["location"] == "/dashboard"
    assert returning.get("/dashboard").status_code == 200

    forger = browser()
    response = forger.get("/auth/token?t=guessed-token", follow_redirects=False)
    assert response.headers["location"] == "/signin?error=bad-link"
    assert forger.get("/api/jobs").status_code == 401


def test_logout_clears_the_session(signed_up):
    signed_up.get("/logout")
    assert signed_up.get("/dashboard", follow_redirects=False).headers["location"] == "/onboard"
    assert signed_up.get("/api/jobs").status_code == 401


def test_duplicate_signup_is_refused(signed_up, browser):
    owner = database.get_user_by_email("ada@example.com")
    friend = browser()
    friend.get(f"/onboard?invite={owner['invite_token']}")

    # Reach the finish step with an email nobody has, then take the account.
    walk_onboarding(friend, email="dupe@example.com")
    database.create_user("Imposter", "dupe@example.com")

    response = friend.post("/api/finish-signup")
    assert response.status_code == 409
    assert "Sign in instead" in response.json()["message"]


def test_settings_shows_invite_and_signin_links(signed_up):
    user = database.get_user_by_email("ada@example.com")
    body = signed_up.get("/settings").text
    assert f"/onboard?invite={user['invite_token']}" in body
    assert f"/auth/token?t={user['login_token']}" in body


def test_settings_updates_criteria(signed_up):
    user = database.get_user_by_email("ada@example.com")
    signed_up.post("/settings", data={
        "job_titles": "Staff PM, Group PM",
        "locations": "Berlin, Remote",
        "remote_preference": "hybrid",
        "min_salary": "150000",
        "max_salary": "",
        "seniority_levels": "Staff",
        "greenhouse_companies": "stripe, linear",
        "lever_companies": "figma",
    })
    criteria = database.get_criteria(user["id"])
    assert criteria["job_titles"] == ["Staff PM", "Group PM"]
    assert criteria["remote_preference"] == "hybrid"
    assert criteria["min_salary"] == 150000
    assert criteria["max_salary"] is None
    assert criteria["greenhouse_companies"] == ["stripe", "linear"]


def test_resume_upload_accepts_plain_text(client):
    walk_onboarding(client, answers=["Ada Lovelace", "ada@example.com",
                                     "PM", "London", "remote", "skip", "skip"])
    response = client.post(
        "/api/resume-upload",
        files={"file": ("resume.txt", b"Ten years of product work.", "text/plain")},
    )
    assert response.json()["action"] == "show_finish"
    client.post("/api/finish-signup")
    user = database.get_user_by_email("ada@example.com")
    assert "Ten years of product work." in user["resume_text"]
