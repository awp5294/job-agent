"""The explainer a friend sees before the first question, and can re-read later."""
import server
from tests.conftest import ONBOARD_ANSWERS

# Claims the page has to make. If one of these disappears, a friend signs up
# without knowing what they agreed to.
PROMISES = [
    "doesn't submit applications",
    "doesn't search LinkedIn",
    "doesn't email anyone but you",
    "doesn't share your data",
    "About the AI key",
    "aistudio.google.com/apikey",
    "stored encrypted",
    "Why it asks what it asks",
]


def test_about_is_readable_without_an_account(client):
    page = client.get("/about")
    assert page.status_code == 200
    for promise in PROMISES:
        assert promise in page.text, promise
    assert "Get started" in page.text
    assert "Sign in" in page.text


def test_about_for_a_signed_in_user_links_back_not_forward(signed_up):
    page = signed_up.get("/about").text
    assert "Dashboard" in page
    assert "Get started" not in page


def test_the_invite_link_lands_on_the_explainer(client):
    page = client.get("/onboard").text
    assert 'id="intro"' in page
    assert 'id="start-btn"' in page
    for promise in PROMISES:
        assert promise in page, promise
    # The chat is on the page, just hidden until Start.
    assert 'id="chat-area" hidden' in page


def test_the_explainer_names_the_sending_address(client, monkeypatch):
    monkeypatch.setattr(server, "mailbox_configured", lambda: True)
    monkeypatch.setattr(server, "mailbox_address", lambda: "jobhunter@example.com")
    page = client.get("/onboard").text
    assert "jobhunter@example.com" in page
    assert "add it to your contacts" in page


def test_a_refresh_mid_chat_goes_back_to_the_chat_not_the_explainer(client):
    client.post("/api/chat", json={"message": ONBOARD_ANSWERS[0]})
    page = client.get("/onboard").text
    assert 'id="intro"' not in page
    assert 'id="chat-area" hidden' not in page
    assert "How this works" in page   # still one click away


def test_a_blocked_signup_still_gets_the_explainer_but_no_start(signed_up, browser):
    """Someone without an invite can read what it is; they just can't begin."""
    stranger = browser()
    page = stranger.get("/onboard").text
    assert "Why it asks what it asks" in page
    assert "invite" in page.lower()
    assert 'id="start-btn"' not in page


def test_dashboard_and_settings_link_to_it(signed_up):
    assert 'href="/about"' in signed_up.get("/dashboard").text
    assert 'href="/about"' in signed_up.get("/settings").text


def test_the_explainer_keeps_to_plain_prose(client):
    """The writing rules the rest of the app holds to."""
    text = client.get("/about").text
    assert "—" not in text.split("<body")[1], "em dash in the explainer"
    for tell in ("leverage", "seamless", "robust", "empower", "streamline", "delve"):
        assert tell not in text.lower(), tell


# ── The explainer is gated on having seen it, not on progress ──────────────

def test_a_half_finished_session_from_before_still_gets_the_explainer(client):
    """Progress without the seen-flag is what a session from before this page
    looks like. They never read it, so show it once."""
    import server
    from db import database
    client.get("/onboard")
    sid = server.signer.loads(client.cookies["session"])
    database.set_session_state(sid, {"step_num": 3, "data": {"name": "Ada"}})

    page = client.get("/onboard").text
    assert 'id="intro"' in page


def test_start_dismisses_the_explainer_for_good(client):
    client.get("/onboard")
    assert client.post("/api/intro-seen").json() == {"ok": True}
    page = client.get("/onboard").text
    assert 'id="intro"' not in page
    assert 'id="chat-area" hidden' not in page


# ── A reload resumes the question the server is actually on ────────────────

def test_a_fresh_session_starts_at_the_first_question(client):
    state = client.get("/api/chat/state").json()
    assert state["step_num"] == 0
    assert "name" in state["reply"].lower()
    assert state["action"] is None


def test_a_reload_mid_chat_resumes_where_the_server_is(client):
    for answer in ONBOARD_ANSWERS[:2]:
        client.post("/api/chat", json={"message": answer})
    state = client.get("/api/chat/state").json()
    assert state["step_num"] == 2
    assert "job titles" in state["reply"].lower()
    # The greeting-by-name prompt was already shown; this one carries no name.
    assert "{name}" not in state["reply"]


def test_a_reload_on_the_key_step_brings_back_the_masked_box(client):
    for answer in ONBOARD_ANSWERS[:8]:
        client.post("/api/chat", json={"message": answer})
    state = client.get("/api/chat/state").json()
    assert state["action"] == "show_secret"
    assert "API key" in state["reply"]


def test_a_reload_after_finishing_points_at_the_dashboard(signed_up):
    """The signed-up fixture's session has walked every step."""
    state = signed_up.get("/api/chat/state").json()
    assert state["action"] == "redirect:/dashboard"


# ── Static assets carry a content hash, so a deploy busts the cache ─────────

def test_static_links_carry_a_version(client):
    """A returning browser must not run yesterday's chat.js after a deploy."""
    import re
    page = client.get("/onboard").text
    for asset in ("style.css", "chat.js"):
        match = re.search(rf"/static/{re.escape(asset)}\?v=([0-9a-f]+)", page)
        assert match, f"{asset} has no ?v= hash"
        assert len(match.group(1)) >= 6


def test_the_version_changes_when_the_file_changes(tmp_path, monkeypatch):
    import server
    css = server.BASE_DIR / "static" / "style.css"
    before = server.static("style.css")
    original = css.read_bytes()
    try:
        css.write_bytes(original + b"\n/* touched */\n")
        after = server.static("style.css")
    finally:
        css.write_bytes(original)
    assert before != after, "editing the file must change its ?v="
    assert server.static("style.css") == before, "reverting restores the hash"
