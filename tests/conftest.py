import os
import uuid

import pytest
from fastapi.testclient import TestClient

import server
from db import database


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Every test gets its own empty database."""
    db_file = tmp_path / f"{uuid.uuid4().hex}.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    database.set_db_path(str(db_file))
    monkeypatch.setattr(server, "DB_PATH", str(db_file))
    database.init_db(str(db_file))
    yield
    database.set_db_path(None)


@pytest.fixture
def client():
    """A browser. Each instance has its own cookie jar."""
    with TestClient(server.app) as c:
        yield c


@pytest.fixture
def browser():
    """Factory for additional independent browsers (i.e. other people)."""
    clients = []

    def _make():
        c = TestClient(server.app)
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


# ── Helpers ────────────────────────────────────────────────────────────────

ONBOARD_ANSWERS = [
    "Ada Lovelace",
    "ada@example.com",
    "Product Manager, Senior PM",
    "London",
    "remote",
    "$120k-$160k",
    "Senior",
    "Ada Lovelace. Ten years building analytical engines and shipping "
    "developer platforms end to end, with a focus on data tooling.",
    "correct-horse-battery",  # password
]


def walk_onboarding(client, answers=None, email=None):
    """Answer every onboarding question. Returns the final chat response body."""
    answers = list(answers or ONBOARD_ANSWERS)
    if email:
        answers[1] = email
    data = {}
    for answer in answers:
        response = client.post("/api/chat", json={"message": answer})
        assert response.status_code == 200, response.text
        data = response.json()
        if (data.get("action") or "").startswith("redirect:"):
            break
    return data


PASSWORD = ONBOARD_ANSWERS[-1]


@pytest.fixture
def signed_up(client):
    """A signed-in owner account (the first user, so no invite needed)."""
    final = walk_onboarding(client)
    assert final.get("action") == "redirect:/dashboard", final
    return client
