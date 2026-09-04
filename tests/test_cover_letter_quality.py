"""Cover letter quality: catching AI tells without damaging the letter.

These letters go to employers, so the two failure modes matter in opposite
directions. Letting slop through is embarrassing. Mangling a real sentence to
remove slop is worse.
"""
from types import SimpleNamespace

import pytest

import llm
from apply.cover_letter import (
    find_slop, generate_cover_letter, stop_slop,
)

GOOD_LETTER = (
    "The payments platform role lines up with the last three years of my work. "
    "At Acme I moved a legacy ledger onto an event-driven design, which cut "
    "settlement from two days to four hours.\n\n"
    "I have run the seam between finance and engineering before, including the "
    "quarterly close that depended on it. Two of my team went on to lead their "
    "own areas.\n\n"
    "The billing infrastructure work you describe is the part I want next."
)


class FakeMessages:
    """Returns a queued list of drafts, one per call."""

    def __init__(self, drafts):
        self.drafts = list(drafts)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.drafts.pop(0) if self.drafts else ""
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
        )


@pytest.fixture
def drafts(monkeypatch):
    def install(*texts):
        messages = FakeMessages(texts)
        monkeypatch.setattr(llm, "get_client", lambda: SimpleNamespace(messages=messages))
        return messages
    return install


# ── The filter must not damage real content ────────────────────────────────

@pytest.mark.parametrize("sentence", [
    "Throughout my career I have shipped 12 products at Stripe, three to 1M+ users.",
    "I rebuilt the dynamic pricing engine, cutting checkout latency by 40%.",
    "As someone who has run payments infra since 2015, I know the failure modes.",
    "I led a robust testing overhaul that took our flake rate from 8% to under 1%.",
    "My team owned the seamless-checkout project through two peak seasons.",
])
def test_real_sentences_survive_the_filter(sentence):
    """Regression: the old filter deleted any sentence containing a stock
    phrase, and rewrote 'dynamic pricing' as 'strong pricing'."""
    assert stop_slop(sentence) == sentence


def test_numbers_and_examples_are_never_touched():
    assert "40%" in stop_slop(GOOD_LETTER + " Latency fell 40%.")
    assert stop_slop(GOOD_LETTER) == GOOD_LETTER


# ── Wrappers the model was told not to add ─────────────────────────────────

def test_a_salutation_is_stripped():
    assert stop_slop("Dear Hiring Manager,\n\nI built the thing.") == "I built the thing."


@pytest.mark.parametrize("sign_off", [
    "\n\nSincerely,\nAda", "\n\nBest regards,\nAda Lovelace", "\n\nThanks,\nAda",
])
def test_a_sign_off_is_stripped(sign_off):
    assert stop_slop("I built the thing." + sign_off) == "I built the thing."


def test_markdown_fences_are_stripped():
    assert stop_slop("```\nI built the thing.\n```") == "I built the thing."


def test_em_dashes_become_commas_without_losing_words():
    cleaned = stop_slop("I own billing — all of it — end to end.")
    assert "—" not in cleaned
    assert "all of it" in cleaned
    assert "end to end" in cleaned


# ── Detection ──────────────────────────────────────────────────────────────

def test_a_clean_letter_reports_nothing():
    assert find_slop(GOOD_LETTER) == []


@pytest.mark.parametrize("draft,expected", [
    ("I am writing to apply for this role.", "I am writing to"),
    ("I am excited about this opportunity.", "excited"),
    ("I am passionate about payments.", "passionate"),
    ("With over 10 years of experience in product.", "N years"),
    ("Throughout my career I have led teams.", "Throughout my career"),
    ("I would welcome the opportunity to discuss.", "welcome the opportunity"),
    ("I look forward to hearing from you.", "look forward to hearing"),
    ("Thank you for considering my application.", "Thank you for considering"),
])
def test_stock_openers_and_closers_are_caught(draft, expected):
    issues = " ".join(find_slop(draft))
    assert expected.lower() in issues.lower()


@pytest.mark.parametrize("word", [
    "leverage", "synergy", "spearhead", "delve", "seamless", "holistic",
    "cutting-edge", "results-driven", "team player", "proven track record",
    "wealth of experience", "hit the ground running", "fast-paced",
])
def test_buzzwords_are_caught(word):
    issues = find_slop(f"I would {word} the role to great effect.")
    assert any(word in issue for issue in issues)


def test_filler_adverbs_are_caught_together():
    issues = find_slop("I truly and deeply improved things significantly.")
    adverb_issue = next(i for i in issues if i.startswith("filler adverbs"))
    for adverb in ("truly", "deeply", "significantly"):
        assert adverb in adverb_issue


def test_em_dashes_and_false_contrasts_are_caught():
    assert any("em dash" in i for i in find_slop("I build things — good ones."))
    assert any("not just" in i for i in
               find_slop("This is not just a job, but a calling."))


def test_vague_claims_are_caught():
    assert any("significant impact" in i for i in
               find_slop("I had significant impact on the team."))
    assert any("wide range" in i for i in
               find_slop("I handled a wide range of responsibilities."))


# ── The revision pass ──────────────────────────────────────────────────────

def test_a_clean_first_draft_costs_one_call(drafts):
    messages = drafts(GOOD_LETTER)
    letter = generate_cover_letter("Staff PM", "Acme", "desc", "resume text")

    assert letter == GOOD_LETTER
    assert len(messages.calls) == 1, "a clean draft should not be rewritten"


def test_a_sloppy_draft_is_sent_back_with_its_problems_named(drafts):
    sloppy = ("I am writing to express my excitement. I leveraged cutting-edge "
              "tools to deliver significant impact.")
    messages = drafts(sloppy, GOOD_LETTER)

    letter = generate_cover_letter("Staff PM", "Acme", "desc", "resume text")

    assert letter == GOOD_LETTER
    assert len(messages.calls) == 2

    revision = messages.calls[1]["messages"][0]["content"]
    assert "I am writing to" in revision
    assert "leverage" in revision
    assert "cutting-edge" in revision
    # The model is told to keep the facts, not just to try again.
    assert "Keep every concrete fact" in revision
    assert sloppy in revision


def test_a_revision_that_does_not_help_is_discarded(drafts):
    """Two bad drafts: keep the first rather than churn."""
    first = "I am writing to apply. I leveraged tools."
    worse = "I am writing to apply. I leveraged cutting-edge tools seamlessly."
    drafts(first, worse)

    letter = generate_cover_letter("Staff PM", "Acme", "desc", "resume")
    assert letter == first


def test_only_one_revision_is_ever_attempted(drafts):
    sloppy = "I am writing to apply, and I leveraged things."
    messages = drafts(sloppy, sloppy, sloppy, sloppy)

    generate_cover_letter("Staff PM", "Acme", "desc", "resume")
    assert len(messages.calls) <= 2, "one revision, then stop paying for retries"


def test_a_failed_revision_keeps_the_draft(monkeypatch):
    """If the second call errors, the applicant still gets their letter."""
    sloppy = "I am writing to apply for this role at Acme."
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text=sloppy)],
            )
        raise llm.LLMError("Rate limited")

    monkeypatch.setattr(
        llm, "get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=flaky)),
    )
    assert generate_cover_letter("Staff PM", "Acme", "desc", "resume") == sloppy


# ── The prompt itself ──────────────────────────────────────────────────────

def test_the_prompt_carries_the_resume_and_the_voice_rules(drafts):
    messages = drafts(GOOD_LETTER)
    generate_cover_letter(
        job_title="Staff PM", company="Acme", job_description="Own billing.",
        resume_text="Ten years in payments infrastructure.",
    )

    system = messages.calls[0]["system"]
    assert "Ten years in payments infrastructure." in system
    assert "No em dashes" in system
    assert "Active voice" in system
    assert "3 short paragraphs" in system
    assert "leverage" in system            # named, not just implied

    user = messages.calls[0]["messages"][0]["content"]
    assert "Staff PM" in user and "Acme" in user and "Own billing." in user
