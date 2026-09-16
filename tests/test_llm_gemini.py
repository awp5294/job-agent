"""The Gemini backend, exercised through the same entry points the app uses."""
import json
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

import llm
from apply.cover_letter import generate_cover_letter
from llm import Credentials, LLMError
from matching.scorer import JobScore, score_job

JOB = {
    "id": 1, "title": "Staff Product Manager", "company": "Stripe",
    "location": "Remote", "remote_type": "remote", "description": "Own payments.",
}
CRITERIA = {"job_titles": ["Product Manager"], "remote_preference": "remote"}


class FakeModels:
    """Stands in for client.models — records the config it was called with."""

    def __init__(self, response=None, raises=None):
        self.response, self.raises = response, raises
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        return self.response


def gemini_response(text=None, parsed=None, block_reason=None, finish_reason=None):
    return SimpleNamespace(
        text=text,
        parsed=parsed,
        prompt_feedback=SimpleNamespace(block_reason=block_reason),
        candidates=[SimpleNamespace(finish_reason=finish_reason)],
    )


@pytest.fixture
def gemini(monkeypatch):
    """Run as if only a Gemini key were configured."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    llm.reset_client()

    def install(response=None, raises=None):
        models = FakeModels(response=response, raises=raises)
        monkeypatch.setattr(llm, "get_client", lambda: SimpleNamespace(models=models))
        return models

    yield install
    llm.reset_client()


def client_error(message: str):
    """A google-genai ClientError without going near the network."""
    return genai_errors.ClientError(
        400, {"error": {"message": message, "status": "INVALID_ARGUMENT"}}
    )


# ── Structured output ──────────────────────────────────────────────────────

def test_scoring_uses_the_gemini_client(gemini):
    models = gemini(gemini_response(parsed=JobScore(score=88, reason="Strong title fit.")))

    score, reason = score_job(JOB, CRITERIA)
    assert (score, reason) == (88, "Strong title fit.")

    call = models.calls[0]
    assert call["model"] == llm.DEFAULT_GEMINI_MODEL
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_schema is JobScore
    assert "Staff Product Manager" in call["contents"]
    assert "score" in call["config"].system_instruction.lower()


def test_raw_json_is_accepted_when_the_sdk_does_not_parse(gemini):
    gemini(gemini_response(text=json.dumps({"score": 75, "reason": "Partial match."})))
    assert score_job(JOB, CRITERIA) == (75, "Partial match.")


def test_json_wrapped_in_a_code_fence_is_accepted(gemini):
    gemini(gemini_response(text='```json\n{"score": 91, "reason": "Great fit."}\n```'))
    assert score_job(JOB, CRITERIA) == (91, "Great fit.")


def test_unparseable_output_is_reported_not_guessed(gemini):
    gemini(gemini_response(text="I think this job is pretty good actually"))
    score, reason = score_job(JOB, CRITERIA)
    assert score == 0
    assert "expected shape" in reason


def test_output_that_breaks_the_schema_is_rejected(gemini):
    gemini(gemini_response(text=json.dumps({"score": 900, "reason": "nope"})))
    score, _ = score_job(JOB, CRITERIA)
    assert score == 0


# ── Prose ──────────────────────────────────────────────────────────────────

def test_cover_letters_use_gemini_and_carry_the_resume(gemini):
    clean = "I led the ledger migration at Acme, cutting settlement time in half."
    models = gemini(gemini_response(text=clean))

    letter = generate_cover_letter(
        job_title="Staff PM", company="Stripe",
        job_description="Own payments.", resume_text="Ten years in fintech.",
    )

    assert letter == clean
    config = models.calls[0]["config"]
    assert "Ten years in fintech." in config.system_instruction
    assert "No em dashes" in config.system_instruction
    assert config.max_output_tokens == 4000


def test_a_sloppy_gemini_draft_gets_one_revision_pass(gemini, monkeypatch):
    """The revision loop is provider-agnostic: same behaviour on Gemini."""
    sloppy = "I am writing to express my excitement. I leveraged cutting-edge tools."
    clean = "I moved the ledger to an event-driven design. Settlement fell to four hours."

    models = FakeModels()
    queue = [sloppy, clean]

    def two_drafts(**kwargs):
        models.calls.append(kwargs)
        return gemini_response(text=queue.pop(0))

    monkeypatch.setattr(models, "generate_content", two_drafts)
    monkeypatch.setattr(llm, "get_client", lambda: SimpleNamespace(models=models))

    letter = generate_cover_letter("Staff PM", "Stripe", "desc", "resume")
    assert letter == clean
    assert len(models.calls) == 2
    assert "leverage" in models.calls[1]["contents"]


def test_an_empty_response_is_an_error(gemini):
    gemini(gemini_response(text=""))
    with pytest.raises(LLMError, match="empty response"):
        generate_cover_letter("PM", "Acme", "desc", "resume")


# ── Blocked and failing calls ──────────────────────────────────────────────

def test_a_blocked_prompt_is_not_treated_as_an_answer(gemini):
    gemini(gemini_response(text="", block_reason="SAFETY"))
    with pytest.raises(LLMError, match="declined"):
        generate_cover_letter("PM", "Acme", "desc", "resume")


def test_a_safety_finish_reason_is_caught(gemini):
    gemini(gemini_response(text="partial", finish_reason="SAFETY"))
    with pytest.raises(LLMError, match="declined"):
        generate_cover_letter("PM", "Acme", "desc", "resume")


@pytest.mark.parametrize("message,expected", [
    ("API key not valid. Please pass a valid API key.", "rejected the API key"),
    ("models/gemini-x is NOT_FOUND for API version v1beta", "GEMINI_MODEL"),
    ("RESOURCE_EXHAUSTED: quota exceeded", "Rate limited"),
])
def test_api_failures_become_actionable_messages(gemini, message, expected):
    gemini(raises=client_error(message))
    with pytest.raises(LLMError, match=expected):
        generate_cover_letter("PM", "Acme", "desc", "resume")


def test_a_scoring_failure_still_does_not_crash_the_digest(gemini):
    gemini(raises=client_error("API key not valid."))
    score, reason = score_job(JOB, CRITERIA)
    assert score == 0
    assert "rejected the API key" in reason


# ── Model auto-discovery when the default isn't on a key ────────────────────
# Free AI Studio keys don't all carry gemini-2.5-flash. The app should find a
# model the key does have rather than failing the whole digest.

from google.genai import errors as genai_errors  # noqa: E402


def _model(name):
    return SimpleNamespace(name=name, supported_actions=["generateContent"])


class DiscoverModels:
    """Rejects the default model, offers others via list()."""
    def __init__(self, available, missing="gemini-2.5-flash"):
        self.available, self.missing, self.calls = available, missing, []

    def list(self):
        return [_model(n) for n in self.available] + [
            SimpleNamespace(name="models/text-embedding-004",
                            supported_actions=["embedContent"])]

    def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        if model == self.missing:
            raise genai_errors.ClientError(
                404, {"error": {"message": f"models/{model} was not found",
                                "status": "NOT_FOUND"}})
        return gemini_response(parsed=JobScore(score=91, reason="fits"),
                               text='{"score":91,"reason":"fits"}')


@pytest.fixture
def discovering(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-discover")
    llm.reset_client()

    def install(available):
        models = DiscoverModels(available)
        client = SimpleNamespace(models=models)
        monkeypatch.setattr(llm, "_build_client", lambda creds: client)
        monkeypatch.setattr(llm, "get_client", lambda: client)
        return models
    yield install
    llm.reset_client()


def test_a_missing_default_falls_back_to_an_available_flash(discovering):
    models = discovering(["models/gemini-2.0-flash", "models/gemini-2.5-pro"])
    result = llm.complete_json(system="s", prompt="p", schema=JobScore,
                               credentials=Credentials("gemini", "AIza-discover"))
    assert result.score == 91
    # Tried the default, then the discovered flash.
    assert models.calls == ["gemini-2.5-flash", "gemini-2.0-flash"]


def test_flash_is_preferred_over_pro_when_both_exist(discovering):
    models = discovering(["models/gemini-2.5-pro", "models/gemini-2.0-flash"])
    llm.complete_json(system="s", prompt="p", schema=JobScore,
                      credentials=Credentials("gemini", "AIza-discover"))
    assert models.calls[-1] == "gemini-2.0-flash"


def test_the_discovered_model_is_reused_not_rediscovered(discovering):
    models = discovering(["models/gemini-2.0-flash"])
    creds = Credentials("gemini", "AIza-discover")
    llm.complete_json(system="s", prompt="p", schema=JobScore, credentials=creds)
    models.calls.clear()
    llm.complete_text(system="s", prompt="p", credentials=creds)
    assert models.calls == ["gemini-2.0-flash"], "should skip the dead default"


def test_a_key_with_no_gemini_model_gives_a_clear_error(discovering):
    discovering([])  # only the embedding model, no generateContent gemini
    with pytest.raises(LLMError, match="no Gemini model available"):
        llm.complete_text(system="s", prompt="p",
                          credentials=Credentials("gemini", "AIza-discover"))
