"""Password handling and the shared mailbox."""
import email as email_module
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest

import server
from auth import PasswordError, hash_password, verify_password
from db import database
from email_handler import mailbox
from tests.conftest import PASSWORD, walk_onboarding


# ── Passwords ──────────────────────────────────────────────────────────────

def test_a_password_verifies_against_its_own_hash():
    stored = hash_password("correct-horse-battery")
    assert verify_password("correct-horse-battery", stored)
    assert not verify_password("Correct-horse-battery", stored)
    assert not verify_password("", stored)


def test_the_same_password_hashes_differently_every_time():
    """Salted, so identical passwords don't produce identical rows."""
    assert hash_password("same-password-twice") != hash_password("same-password-twice")


def test_the_plaintext_never_appears_in_the_hash():
    assert "correct-horse-battery" not in hash_password("correct-horse-battery")


def test_short_passwords_are_refused():
    with pytest.raises(PasswordError, match="at least 8"):
        hash_password("short")


def test_a_corrupt_or_missing_hash_fails_closed():
    for stored in [None, "", "not-a-hash", "scrypt$bad$fields", "md5$1$2$3$4$5"]:
        assert verify_password("anything", stored) is False


# ── Signing in ─────────────────────────────────────────────────────────────

def test_the_password_from_onboarding_signs_you_back_in(signed_up, browser):
    returning = browser()
    response = returning.post(
        "/api/signin", json={"email": "ada@example.com", "password": PASSWORD}
    )
    assert response.json()["ok"] is True
    assert returning.get("/dashboard").status_code == 200


@pytest.mark.parametrize("email,password", [
    ("ada@example.com", "not-the-password"),
    ("nobody@example.com", PASSWORD),
    ("", ""),
])
def test_bad_credentials_are_refused(signed_up, browser, email, password):
    attacker = browser()
    response = attacker.post("/api/signin", json={"email": email, "password": password})
    assert response.status_code == 401
    assert attacker.get("/api/jobs").status_code == 401


def test_the_error_does_not_reveal_whether_the_email_exists(signed_up, browser):
    known = browser().post(
        "/api/signin", json={"email": "ada@example.com", "password": "wrong"}
    ).json()["message"]
    unknown = browser().post(
        "/api/signin", json={"email": "nobody@example.com", "password": "wrong"}
    ).json()["message"]
    assert known == unknown


def test_the_password_is_not_stored_in_the_clear(signed_up):
    user = database.get_user_by_email("ada@example.com")
    assert user["password_hash"]
    assert PASSWORD not in user["password_hash"]
    assert verify_password(PASSWORD, user["password_hash"])


def test_a_weak_password_is_rejected_during_onboarding(client):
    walk_onboarding(client, answers=["Ada", "ada@example.com", "PM", "London",
                                     "remote", "skip", "skip", "x" * 60])
    response = client.post("/api/chat", json={"message": "abc"})
    assert "at least 8" in response.json()["reply"]
    assert response.json()["action"] is None
    assert database.count_users() == 0

    # A good one straight after still works.
    ok = client.post("/api/chat", json={"message": "a-decent-password"})
    assert ok.json()["action"] == "redirect:/dashboard"
    assert database.count_users() == 1


def test_changing_your_password_needs_the_old_one(signed_up):
    wrong = signed_up.post("/api/change-password", json={
        "current_password": "not-it", "new_password": "brand-new-password"
    })
    assert wrong.status_code == 403

    right = signed_up.post("/api/change-password", json={
        "current_password": PASSWORD, "new_password": "brand-new-password"
    })
    assert right.json()["ok"] is True

    user = database.get_user_by_email("ada@example.com")
    assert verify_password("brand-new-password", user["password_hash"])
    assert not verify_password(PASSWORD, user["password_hash"])


def test_a_new_password_must_still_be_strong_enough(signed_up):
    response = signed_up.post("/api/change-password", json={
        "current_password": PASSWORD, "new_password": "abc"
    })
    assert response.status_code == 400
    assert "at least 8" in response.json()["message"]


def test_changing_a_password_requires_being_signed_in(browser, signed_up):
    stranger = browser()
    response = stranger.post("/api/change-password", json={
        "current_password": PASSWORD, "new_password": "brand-new-password"
    })
    assert response.status_code == 401


# ── Mailbox ────────────────────────────────────────────────────────────────

@pytest.fixture
def configured_mailbox(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "jobagent@example.com")
    monkeypatch.setenv("SMTP_PASS", "app-password")
    monkeypatch.delenv("MAIL_FROM", raising=False)


def test_mailbox_reports_whether_it_can_send(monkeypatch, configured_mailbox):
    assert mailbox.mailbox_configured()
    assert mailbox.mailbox_address() == "jobagent@example.com"

    monkeypatch.delenv("SMTP_PASS")
    assert not mailbox.mailbox_configured()


def test_sending_without_a_mailbox_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASS", raising=False)
    with pytest.raises(mailbox.MailboxError, match="No mailbox configured"):
        mailbox.send_email("someone@example.com", "Hi", "text", "<p>html</p>")


def test_replies_come_back_to_the_app_mailbox(configured_mailbox):
    """Reply-To must be the app's own address, since that's the inbox we read."""
    message = mailbox.build_message("friend@example.com", "Your job digest",
                                    "text body", "<p>html body</p>")
    assert message["To"] == "friend@example.com"
    assert message["From"] == "jobagent@example.com"
    assert message["Reply-To"] == "jobagent@example.com"
    assert {part.get_content_type() for part in message.get_payload()} == {
        "text/plain", "text/html"
    }


def test_a_separate_from_address_can_be_used(monkeypatch, configured_mailbox):
    monkeypatch.setenv("MAIL_FROM", "jobs@mydomain.com")
    message = mailbox.build_message("friend@example.com", "s", "t", "h")
    assert message["From"] == "jobs@mydomain.com"


def test_imap_defaults_follow_the_smtp_login(configured_mailbox):
    settings = mailbox.imap_settings()
    assert settings["host"] == "imap.gmail.com"
    assert settings["user"] == "jobagent@example.com"
    assert settings["password"] == "app-password"


# ── Parsing what comes back ────────────────────────────────────────────────

def _message(body: str, content_type: str = "plain") -> object:
    outer = MIMEMultipart("alternative")
    outer["Subject"] = "Re: Your job digest"
    outer["From"] = "Ada Lovelace <ada@example.com>"
    outer.attach(MIMEText(body, content_type))
    return email_module.message_from_string(outer.as_string())


def test_the_plain_text_part_is_the_one_read():
    message = MIMEMultipart("alternative")
    message.attach(MIMEText("1, 3", "plain"))
    message.attach(MIMEText("<p>9, 9, 9</p>", "html"))
    parsed = email_module.message_from_string(message.as_string())
    assert "1, 3" in mailbox._plain_body(parsed)


def test_quoted_history_is_dropped_before_parsing():
    reply = """1 and 3 please

On Tue, Job Agent wrote:
> 1. Staff Product Manager
> 2. Senior PM
> 4. Another one
"""
    assert mailbox.extract_reply_numbers(reply, max_number=4) == [1, 3]
    assert "Staff Product Manager" not in mailbox.strip_quoted(reply)


def test_numbers_outside_the_digest_are_ignored():
    assert mailbox.extract_reply_numbers("2 and 99", max_number=3) == [2]
    assert mailbox.extract_reply_numbers("no thanks", max_number=3) == []


def test_a_body_that_is_only_a_quote_yields_nothing():
    assert mailbox.extract_reply_numbers("> 1. A job\n> 2. Another", 5) == []
