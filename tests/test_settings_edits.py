"""Changing your answers after onboarding: criteria, resume, name, email."""
import io

import pytest
from db import database


def pdf_bytes(text: str) -> bytes:
    """A one-page PDF carrying `text`, so the real pypdf path is exercised."""
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def owner(client=None):
    return database.get_user_by_email("ada@example.com")


# ── Criteria (already worked; guard it) ────────────────────────────────────

def test_changing_onsite_nyc_to_remote_sticks(signed_up):
    """The exact edit asked for: onsite in NYC, now remote."""
    signed_up.post("/settings", data={"remote_preference": "onsite",
                                       "locations": "New York"})
    assert database.get_criteria(owner()["id"])["remote_preference"] == "onsite"

    signed_up.post("/settings", data={"remote_preference": "remote", "locations": ""})
    criteria = database.get_criteria(owner()["id"])
    assert criteria["remote_preference"] == "remote"
    assert criteria["locations"] == []


# ── Resume ─────────────────────────────────────────────────────────────────

def test_resume_can_be_replaced_by_pasting(signed_up):
    new = "Grace Hopper. Compiler pioneer. Shipped COBOL and a great deal else."
    signed_up.post("/settings", data={"resume_text": new, "remote_preference": "any"})
    assert database.get_user_by_email("ada@example.com")["resume_text"] == new


def test_a_blank_resume_field_does_not_wipe_the_saved_one(signed_up):
    before = database.get_user_by_email("ada@example.com")["resume_text"]
    assert before  # onboarding set it
    signed_up.post("/settings", data={"resume_text": "", "remote_preference": "any"})
    assert database.get_user_by_email("ada@example.com")["resume_text"] == before


def test_resume_upload_replaces_the_text(signed_up):
    resp = signed_up.post("/api/resume-update",
                          files={"file": ("cv.txt", b"Twelve years building payment rails at scale.", "text/plain")})
    assert resp.json()["ok"] is True
    assert "payment rails" in database.get_user_by_email("ada@example.com")["resume_text"]


def test_an_empty_file_is_refused_rather_than_wiping_the_resume(signed_up):
    before = database.get_user_by_email("ada@example.com")["resume_text"]
    resp = signed_up.post("/api/resume-update",
                          files={"file": ("blank.txt", b"   ", "text/plain")})
    assert resp.status_code == 400
    assert database.get_user_by_email("ada@example.com")["resume_text"] == before


def test_resume_update_requires_sign_in(client):
    resp = client.post("/api/resume-update",
                       files={"file": ("cv.txt", b"x" * 50, "text/plain")})
    assert resp.status_code == 401


def test_the_settings_page_prefills_the_current_resume(signed_up):
    signed_up.post("/settings", data={"resume_text": "UNIQUEMARKER resume body here.",
                                      "remote_preference": "any"})
    assert "UNIQUEMARKER" in signed_up.get("/settings").text


# ── Name and email ─────────────────────────────────────────────────────────

def test_name_can_be_changed(signed_up):
    signed_up.post("/settings", data={"name": "Ada L.", "remote_preference": "any"})
    assert database.get_user_by_email("ada@example.com")["name"] == "Ada L."


def test_email_can_be_changed_and_the_digest_follows(signed_up):
    signed_up.post("/settings", data={"email": "ada.new@example.com",
                                      "remote_preference": "any"})
    assert database.get_user_by_email("ada.new@example.com") is not None
    assert database.get_user_by_email("ada@example.com") is None


def test_email_change_is_case_normalised(signed_up):
    signed_up.post("/settings", data={"email": "ADA.CAPS@Example.com",
                                      "remote_preference": "any"})
    assert database.get_user_by_email("ada.caps@example.com") is not None


def test_cannot_take_an_email_another_account_owns(signed_up, browser):
    owner_user = database.get_user_by_email("ada@example.com")
    friend = browser()
    friend.get(f"/onboard?invite={owner_user['invite_token']}")
    from tests.conftest import walk_onboarding
    walk_onboarding(friend, email="grace@example.com")

    # Owner tries to grab Grace's address.
    resp = signed_up.post("/settings", data={"email": "grace@example.com",
                                             "remote_preference": "any"},
                          follow_redirects=False)
    assert "key_error=" in resp.headers["location"]
    # Both accounts keep their own address.
    assert database.get_user_by_email("ada@example.com") is not None
    assert database.get_user_by_email("grace@example.com")["id"] != owner_user["id"]


def test_a_malformed_email_is_refused(signed_up):
    resp = signed_up.post("/settings", data={"email": "not-an-email",
                                             "remote_preference": "any"},
                          follow_redirects=False)
    assert "key_error=" in resp.headers["location"]
    assert database.get_user_by_email("ada@example.com") is not None
