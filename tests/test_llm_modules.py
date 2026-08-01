"""The Claude-backed pieces: the scoring wrapper and the cover-letter writer."""
from types import SimpleNamespace

import anthropic
import httpx
import pytest

import llm
from apply.cover_letter import generate_cover_letter, stop_slop
from llm import LLMError
from matching.scorer import JobScore, score_job, score_jobs_for_user

JOB = {
    "id": 1, "title": "Staff Product Manager", "company": "Stripe",
    "location": "Remote", "remote_type": "remote", "description": "Own payments.",
}
CRITERIA = {"job_titles": ["Product Manager"], "locations": [], "remote_preference": "remote"}


class FakeMessages:
    def __init__(self, parsed=None, text=None, stop_reason="end_turn"):
        self.parsed, self.text, self.stop_reason = parsed, text, stop_reason
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=self.parsed)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        blocks = [SimpleNamespace(type="text", text=self.text or "")]
        return SimpleNamespace(stop_reason=self.stop_reason, content=blocks)


@pytest.fixture
def fake_llm(monkeypatch):
    def install(**kwargs):
        messages = FakeMessages(**kwargs)
        monkeypatch.setattr(llm, "get_client", lambda: SimpleNamespace(messages=messages))
        return messages
    return install


# ── llm wrapper ────────────────────────────────────────────────────────────

def test_missing_api_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm.reset_client()
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY is not set"):
        llm.get_client()
    llm.reset_client()


def test_api_errors_become_llm_errors(monkeypatch):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.RateLimitError(
        "slow down", response=httpx.Response(429, request=request), body=None
    )

    def raiser(**kwargs):
        raise error

    monkeypatch.setattr(llm, "get_client",
                        lambda: SimpleNamespace(messages=SimpleNamespace(create=raiser)))
    with pytest.raises(LLMError, match="Rate limited"):
        llm.complete_text(system="s", prompt="p")


def test_a_refusal_is_not_treated_as_an_answer(fake_llm):
    fake_llm(text="", stop_reason="refusal")
    with pytest.raises(LLMError, match="declined"):
        llm.complete_text(system="s", prompt="p")


# ── Scorer ─────────────────────────────────────────────────────────────────

def test_score_job_returns_the_models_verdict(fake_llm):
    messages = fake_llm(parsed=JobScore(score=93, reason="Title and remote policy match."))
    score, reason = score_job(JOB, CRITERIA)

    assert (score, reason) == (93, "Title and remote policy match.")
    call = messages.calls[0]
    assert call["model"] == llm.MODEL
    assert call["output_format"] is JobScore
    # Scoring is a bounded classification — no reason to pay for deep reasoning.
    assert call["output_config"]["effort"] == "low"
    assert "Staff Product Manager" in call["messages"][0]["content"]
    assert "Product Manager" in call["messages"][0]["content"]


def test_a_scoring_failure_does_not_crash_the_digest(monkeypatch):
    def raiser(**kwargs):
        raise LLMError("Anthropic is down")

    monkeypatch.setattr("matching.scorer.complete_json", raiser)
    score, reason = score_job(JOB, CRITERIA)
    assert score == 0
    assert "Anthropic is down" in reason


def test_only_jobs_over_the_threshold_are_kept(monkeypatch):
    scores = iter([(91, "Great"), (55, "Meh"), (70, "Borderline")])
    monkeypatch.setattr("matching.scorer.score_job", lambda job, criteria: next(scores))

    jobs = [dict(JOB, id=i, title=f"Job {i}") for i in (1, 2, 3)]
    kept = score_jobs_for_user(jobs, user_id=1, criteria=CRITERIA)

    assert [(j["title"], s) for j, s, _ in kept] == [("Job 1", 91), ("Job 3", 70)]


def test_jobs_that_failed_to_store_are_skipped(monkeypatch):
    monkeypatch.setattr("matching.scorer.score_job",
                        lambda job, criteria: pytest.fail("should not be scored"))
    assert score_jobs_for_user([{"title": "No id"}], 1, CRITERIA) == []


# ── Cover letters ──────────────────────────────────────────────────────────

def test_cover_letter_uses_the_resume_and_strips_slop(fake_llm):
    messages = fake_llm(text=(
        "I am truly passionate about payments infrastructure. "
        "I led the migration of a legacy ledger to a robust event-driven design.\n\n"
        "We can leverage that experience here."
    ))

    letter = generate_cover_letter(
        job_title="Staff PM",
        company="Stripe",
        job_description="Own payments platform strategy.",
        resume_text="Ten years shipping developer platforms.",
    )

    assert "passionate" not in letter
    assert "leverage" not in letter
    assert "strong event-driven design" in letter
    assert "use that experience here" in letter

    system = messages.calls[0]["system"]
    assert "Ten years shipping developer platforms." in system
    assert messages.calls[0]["model"] == llm.MODEL
    assert "Stripe" in messages.calls[0]["messages"][0]["content"]


def test_cover_letter_failures_surface_to_the_caller(fake_llm):
    fake_llm(text="", stop_reason="refusal")
    with pytest.raises(LLMError):
        generate_cover_letter("Staff PM", "Stripe", "desc", "resume")


@pytest.mark.parametrize("raw,gone", [
    ("I am excited to apply. I shipped three products.", "excited"),
    ("As someone who loves data, I dig in. I shipped three products.", "As someone who"),
    ("This is a results-driven role. I shipped three products.", "results-driven"),
])
def test_stop_slop_removes_offending_sentences_only(raw, gone):
    cleaned = stop_slop(raw)
    assert gone not in cleaned
    assert "I shipped three products." in cleaned


def test_stop_slop_leaves_clean_prose_alone():
    text = "I led the ledger migration at Acme. It cut settlement time in half."
    assert stop_slop(text) == text
