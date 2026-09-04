"""Each person's jobs run on their own AI key, never on the owner's.

Covers the sealed storage, provider detection, the onboarding step, Settings,
and that the right key reaches the scorer and the cover-letter writer.
"""
from types import SimpleNamespace

import pytest

import llm
import server
from db import database
from llm import Credentials, LLMError
from secretbox import SecretBoxError, seal, unseal
from tests.conftest import ONBOARD_ANSWERS, walk_onboarding

GEMINI_KEY = "AIzaSyFakeFakeFakeFakeFakeFakeFakeFake12"
CLAUDE_KEY = "sk-ant-api03-fakefakefakefakefakefakefake"


@pytest.fixture
def no_network(monkeypatch):
    """Key checks talk to the provider; here they just record what they saw."""
    seen = []
    monkeypatch.setattr(server, "verify_credentials", lambda c: seen.append(c))
    return seen


def friend_answers(email="grace@example.com", key="skip"):
    answers = list(ONBOARD_ANSWERS)
    answers[0] = "Grace Hopper"
    answers[1] = email
    answers[-2] = key
    return answers


def invite_a_friend(signed_up, browser, key="skip"):
    owner = database.get_user_by_email("ada@example.com")
    friend = browser()
    friend.get(f"/onboard?invite={owner['invite_token']}")
    final = walk_onboarding(friend, friend_answers(key=key))
    assert final.get("action") == "redirect:/dashboard", final
    return friend, database.get_user_by_email("grace@example.com")


# ── Sealed storage ─────────────────────────────────────────────────────────

def test_a_sealed_key_round_trips_and_is_not_readable_in_the_database():
    token = seal(GEMINI_KEY)
    assert GEMINI_KEY not in token
    assert unseal(token) == GEMINI_KEY


def test_two_seals_of_the_same_key_differ():
    """No way to tell from the database that two people used the same key."""
    assert seal(GEMINI_KEY) != seal(GEMINI_KEY)


def test_changing_secret_key_makes_old_seals_unreadable_not_wrong(monkeypatch):
    token = seal(GEMINI_KEY)
    monkeypatch.setenv("SECRET_KEY", "a-different-secret")
    with pytest.raises(SecretBoxError, match="SECRET_KEY"):
        unseal(token)


# ── Which provider a key belongs to ────────────────────────────────────────

@pytest.mark.parametrize("key, provider", [
    (GEMINI_KEY, "gemini"),
    (CLAUDE_KEY, "anthropic"),
    (f"  {CLAUDE_KEY}\n", "anthropic"),
])
def test_provider_is_read_off_the_key(key, provider):
    assert llm.detect_provider(key) == provider


@pytest.mark.parametrize("garbage", ["hunter2", "", "pk_live_stripe_key", "gemini"])
def test_anything_else_is_refused_with_a_hint(garbage):
    with pytest.raises(LLMError, match="AIza"):
        llm.detect_provider(garbage)


def test_a_call_with_credentials_uses_that_key_not_the_servers(monkeypatch):
    built = []

    def fake_build(credentials):
        built.append(credentials)
        messages = SimpleNamespace(
            create=lambda **kw: SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="Dear team,")],
            )
        )
        return SimpleNamespace(messages=messages)

    monkeypatch.setattr(llm, "_build_client", fake_build)
    monkeypatch.setattr(llm, "get_client", lambda: pytest.fail("used the server key"))

    text = llm.complete_text(system="s", prompt="p",
                             credentials=Credentials("anthropic", CLAUDE_KEY))
    assert text == "Dear team,"
    assert built == [Credentials("anthropic", CLAUDE_KEY)]


def test_verifying_a_key_asks_the_provider_without_generating(monkeypatch):
    calls = []
    fake = SimpleNamespace(models=SimpleNamespace(list=lambda **kw: calls.append(kw) or []))
    monkeypatch.setattr(llm, "_build_client", lambda c: fake)
    llm.verify_credentials(Credentials("anthropic", CLAUDE_KEY))
    assert calls == [{"limit": 1}]


# ── Onboarding ─────────────────────────────────────────────────────────────

def test_onboarding_asks_for_a_key_and_stores_it_sealed(client, no_network):
    final = walk_onboarding(client, friend_answers(email="ada@example.com", key=GEMINI_KEY))
    assert final.get("action") == "redirect:/dashboard", final

    user = database.get_user_by_email("ada@example.com")
    assert user["llm_api_key"]
    assert GEMINI_KEY not in user["llm_api_key"]
    assert unseal(user["llm_api_key"]) == GEMINI_KEY
    assert [c.provider for c in no_network] == ["gemini"]


def test_a_key_that_looks_wrong_is_refused_before_anyone_is_called(client, no_network):
    answers = friend_answers(email="ada@example.com", key="not-a-key")
    data = None
    for answer in answers[:-1]:      # up to and including the bad key
        data = client.post("/api/chat", json={"message": answer}).json()
    assert "AIza" in data["reply"]
    assert data["step_num"] == server.STEP_INDEX["api_key"], "should not have advanced"
    assert no_network == []


def test_a_key_the_provider_rejects_is_refused_and_can_be_retried(client, monkeypatch):
    def reject(credentials):
        raise LLMError("Google rejected the API key.")
    monkeypatch.setattr(server, "verify_credentials", reject)

    answers = friend_answers(email="ada@example.com", key=GEMINI_KEY)
    for answer in answers[:-1]:
        data = client.post("/api/chat", json={"message": answer}).json()
    assert "rejected" in data["reply"]
    assert data["step_num"] == server.STEP_INDEX["api_key"]

    # Type skip instead and finish. No key must have been kept from the attempt.
    client.post("/api/chat", json={"message": "skip"})
    final = client.post("/api/chat", json={"message": answers[-1]}).json()
    assert final["action"] == "redirect:/dashboard"
    assert database.get_user_by_email("ada@example.com")["llm_api_key"] is None


def test_the_key_step_uses_the_masked_input(client):
    for answer in ONBOARD_ANSWERS[:7]:
        client.post("/api/chat", json={"message": answer})
    data = client.post("/api/chat", json={"message": ONBOARD_ANSWERS[7]}).json()
    assert data["action"] == "show_secret"


def test_a_friend_who_skips_is_told_to_add_a_key(signed_up, browser):
    owner = database.get_user_by_email("ada@example.com")
    friend = browser()
    friend.get(f"/onboard?invite={owner['invite_token']}")
    final = walk_onboarding(friend, friend_answers())
    assert "add your AI key in Settings" in final["reply"]

    page = friend.get("/dashboard").text
    assert "Add your AI key before running a digest" in page


def test_the_owner_who_skips_is_not_nagged(signed_up):
    """The server key is theirs. Nothing to add."""
    page = signed_up.get("/dashboard").text
    assert "Add your AI key" not in page


# ── Whose key gets used ────────────────────────────────────────────────────

def test_the_owner_without_a_personal_key_uses_the_servers(signed_up, monkeypatch):
    owner = database.get_user_by_email("ada@example.com")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server-key")
    credentials = server.credentials_for_user(owner)
    assert credentials == Credentials("anthropic", "sk-ant-server-key")


def test_a_friend_without_a_key_never_falls_through_to_the_servers(signed_up, browser):
    _, friend = invite_a_friend(signed_up, browser, key="skip")
    with pytest.raises(LLMError, match="Settings"):
        server.credentials_for_user(friend)


def test_a_friends_own_key_is_the_one_used(signed_up, browser, no_network):
    _, friend = invite_a_friend(signed_up, browser, key=GEMINI_KEY)
    assert server.credentials_for_user(friend) == Credentials("gemini", GEMINI_KEY)


def test_a_key_sealed_under_an_old_secret_asks_to_be_re_entered(signed_up, monkeypatch):
    owner = database.get_user_by_email("ada@example.com")
    database.update_user(owner["id"], {"llm_api_key": seal(GEMINI_KEY)})
    monkeypatch.setenv("SECRET_KEY", "rotated")
    with pytest.raises(LLMError, match="Add it again in Settings"):
        server.credentials_for_user(database.get_user(owner["id"]))


# ── Where the key lands ────────────────────────────────────────────────────

def test_a_digest_for_a_keyless_friend_fails_fast_and_pulls_nothing(
        signed_up, browser, monkeypatch):
    friend_client, friend = invite_a_friend(signed_up, browser)
    monkeypatch.setattr(server, "_collect_jobs",
                        lambda c: pytest.fail("sourced jobs with nothing to score them"))

    import asyncio
    result = asyncio.run(server.run_digest_for_user(friend["id"]))
    assert result["status"] == "error"

    status = friend_client.get("/api/digest-status").json()
    assert status["status"] == "error"
    assert "Settings" in status["message"]


def test_scoring_receives_the_users_own_credentials(signed_up, browser, no_network, monkeypatch):
    _, friend = invite_a_friend(signed_up, browser, key=GEMINI_KEY)
    monkeypatch.setattr(server, "_collect_jobs", lambda c: ([], []))
    received = []

    def fake_score(all_jobs, user_id, criteria, credentials=None):
        received.append(credentials)
        return []
    monkeypatch.setattr(server, "score_jobs_for_user", fake_score)

    import asyncio
    asyncio.run(server.run_digest_for_user(friend["id"]))
    assert received == [Credentials("gemini", GEMINI_KEY)]


def test_selecting_a_job_without_a_key_says_so_instead_of_502(signed_up, browser):
    friend_client, friend = invite_a_friend(signed_up, browser)
    job_id = database.upsert_job("greenhouse", "x1", "PM", "Acme", "https://a.example/1")
    database.upsert_user_job(friend["id"], job_id, 90, "fits")
    uj = database.get_user_jobs(friend["id"])[0]

    response = friend_client.post(f"/api/jobs/{uj['id']}/select")
    assert response.status_code == 400
    assert "Settings" in response.json()["detail"]


# ── Settings ───────────────────────────────────────────────────────────────

def test_settings_adds_a_key_and_shows_it_masked(signed_up, no_network):
    response = signed_up.post("/settings", data={"llm_api_key": CLAUDE_KEY,
                                                  "remote_preference": "any"},
                              follow_redirects=False)
    assert response.status_code == 303
    page = signed_up.get("/settings").text
    assert CLAUDE_KEY not in page
    assert "sk-a…fake" in page

    owner = database.get_user_by_email("ada@example.com")
    assert unseal(owner["llm_api_key"]) == CLAUDE_KEY


def test_settings_leaves_the_key_alone_when_the_field_is_blank(signed_up, no_network):
    signed_up.post("/settings", data={"llm_api_key": CLAUDE_KEY, "remote_preference": "any"})
    signed_up.post("/settings", data={"llm_api_key": "", "remote_preference": "remote"})
    owner = database.get_user_by_email("ada@example.com")
    assert unseal(owner["llm_api_key"]) == CLAUDE_KEY
    assert database.get_criteria(owner["id"])["remote_preference"] == "remote"


def test_settings_removes_a_key(signed_up, no_network):
    signed_up.post("/settings", data={"llm_api_key": CLAUDE_KEY, "remote_preference": "any"})
    signed_up.post("/settings", data={"remove_api_key": "1", "remote_preference": "any"})
    assert database.get_user_by_email("ada@example.com")["llm_api_key"] is None


def test_a_bad_key_in_settings_keeps_the_other_changes_and_explains(signed_up, no_network):
    response = signed_up.post("/settings", data={"llm_api_key": "nope",
                                                  "remote_preference": "onsite"},
                              follow_redirects=False)
    assert "key_error=" in response.headers["location"]
    page = signed_up.get(response.headers["location"]).text
    assert "AI key wasn't" in page
    owner = database.get_user_by_email("ada@example.com")
    assert owner["llm_api_key"] is None
    assert database.get_criteria(owner["id"])["remote_preference"] == "onsite"


def test_mask_shows_just_enough_to_recognise():
    assert server.mask_key("AIzaSyABCDEFGHIJKLMNOPk3f9") == "AIza…k3f9"
    assert server.mask_key("short") == "•••••"
