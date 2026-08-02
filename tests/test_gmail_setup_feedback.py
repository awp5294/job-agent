"""What the app tells you when Gmail isn't set up.

Getting this wrong is worse than it sounds: without Gmail there is no digest and
no reply-to-apply, so a silent failure here means the whole product does nothing
and never says why.
"""
import server


def test_connect_gmail_explains_itself_instead_of_bouncing(signed_up, monkeypatch):
    """Regression: /auth/gmail redirected to /signin, which redirected a
    signed-in user straight back to /dashboard — an invisible round trip."""
    monkeypatch.setattr(server, "oauth_configured", lambda: False)

    response = signed_up.get("/auth/gmail", follow_redirects=False)
    assert response.headers["location"] == "/signin?error=gmail-not-configured"

    landing = signed_up.get("/signin?error=gmail-not-configured", follow_redirects=False)
    assert landing.status_code == 200, "signed-in users must still see the reason"
    assert "GMAIL_CLIENT_ID" in landing.text


def test_signin_still_redirects_a_signed_in_user_with_nothing_to_say(signed_up):
    response = signed_up.get("/signin", follow_redirects=False)
    assert response.headers["location"] == "/dashboard"


def test_dashboard_does_not_offer_a_link_that_goes_nowhere(signed_up, monkeypatch):
    monkeypatch.setattr(server, "oauth_configured", lambda: False)
    body = signed_up.get("/dashboard").text

    assert 'href="/auth/gmail"' not in body
    assert "isn't set up on this deployment" in body
    assert "GMAIL_CLIENT_ID" in body


def test_dashboard_offers_the_link_once_gmail_is_configured(signed_up, monkeypatch):
    monkeypatch.setattr(server, "oauth_configured", lambda: True)
    body = signed_up.get("/dashboard").text

    assert 'href="/auth/gmail"' in body
    assert "isn't set up on this deployment" not in body


def test_no_banner_at_all_once_gmail_is_connected(signed_up, monkeypatch):
    from db import database

    monkeypatch.setattr(server, "oauth_configured", lambda: True)
    user = database.get_user_by_email("ada@example.com")
    database.update_user(user["id"], {"gmail_credentials": '{"token": "x"}'})

    body = signed_up.get("/dashboard").text
    assert "digest emails can't be sent" not in body


def test_settings_says_the_same_thing(signed_up, monkeypatch):
    monkeypatch.setattr(server, "oauth_configured", lambda: False)
    body = signed_up.get("/settings").text
    assert "GMAIL_CLIENT_ID" in body
