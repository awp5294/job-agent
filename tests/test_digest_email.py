"""What the digest email actually looks like, and how replies to it are read."""
import pytest

from email_handler.digest import (
    build_digest_html, build_digest_text, digest_subject, summarise,
)
from email_handler.mailbox import extract_reply_numbers

JOBS = [
    {
        "title": "Staff Product Manager, Payments",
        "company": "Anthropic",
        "location": "San Francisco / Remote",
        "salary_min": 180000, "salary_max": 240000,
        "score": 93, "score_reason": "Title, remote policy and salary all match.",
        "apply_url": "https://boards.greenhouse.io/anthropic/jobs/1",
        "description": "<p>Own the <b>payments platform</b> roadmap end to end, "
                       "partnering with engineering and finance.</p>",
    },
    {
        "title": "Senior PM, Growth",
        "company": "Figma",
        "location": "Remote (US)",
        "salary_min": None, "salary_max": None,
        "score": 81, "score_reason": "Right seniority, salary not listed.",
        "apply_url": "https://jobs.lever.co/figma/2",
        "description": "Own the growth surface area.",
    },
]


# ── The digest ─────────────────────────────────────────────────────────────

def test_every_job_is_numbered_and_clickable():
    """Regression: the HTML digest had no link at all — only the plain-text
    version did, so most people saw a list they couldn't act on."""
    html = build_digest_html("Anthony", JOBS)
    for i, job in enumerate(JOBS, 1):
        assert f"{i}. {job['title']}" in html
        assert f'href="{job["apply_url"]}"' in html


def test_the_email_carries_company_description_location_and_salary():
    html = build_digest_html("Anthony", JOBS)
    assert "Anthropic" in html
    assert "payments platform roadmap" in html      # description, tags stripped
    assert "San Francisco / Remote" in html          # location
    assert "$180,000–$240,000" in html               # salary
    assert "93% match" in html
    assert "Title, remote policy and salary all match." in html


def test_a_job_without_a_salary_still_renders():
    html = build_digest_html("Anthony", [JOBS[1]])
    assert "Remote (US)" in html
    assert "$" not in html.split("Remote (US)")[1].split("match")[0]


def test_the_plain_text_version_has_the_same_facts():
    text = build_digest_text("Anthony", JOBS)
    assert "1. Staff Product Manager, Payments — Anthropic" in text
    assert "San Francisco / Remote · $180,000–$240,000" in text
    assert "payments platform roadmap" in text
    assert "https://boards.greenhouse.io/anthropic/jobs/1" in text
    assert "2. Senior PM, Growth — Figma" in text


def test_the_email_says_how_to_reply():
    for body in (build_digest_html("A", JOBS), build_digest_text("A", JOBS)):
        assert "Reply" in body
        assert "1, 3 and 5" in body
        assert "all" in body


def test_the_subject_says_how_many():
    assert digest_subject(JOBS).startswith("Your job digest — 2 matches")


def test_scraped_html_cannot_break_the_email():
    """Titles come from third-party boards, so they get escaped."""
    nasty = dict(JOBS[0], title='Staff PM <script>alert("x")</script>',
                 company='A & B "Corp"')
    html = build_digest_html("Anthony", [nasty])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "A &amp; B" in html


# ── Description snippets ───────────────────────────────────────────────────

def test_html_descriptions_become_readable_text():
    assert summarise("<p>Own the <b>roadmap</b>.</p>") == "Own the roadmap."
    assert summarise("<li>Ship it</li><li>Measure it</li>") == "Ship it Measure it"


def test_entities_are_decoded():
    assert summarise("Research &amp; development") == "Research & development"


def test_long_descriptions_are_cut_on_a_word_boundary():
    snippet = summarise(" ".join(["word"] * 200), limit=50)
    assert len(snippet) <= 51
    assert snippet.endswith("…")
    assert "wor…" not in snippet


def test_an_empty_description_is_not_an_error():
    assert summarise("") == ""
    assert summarise(None) == ""


# ── Reading the reply ──────────────────────────────────────────────────────

@pytest.mark.parametrize("reply,expected", [
    ("apply to jobs 1 2 5", [1, 2, 5]),
    ("1, 3, 5", [1, 3, 5]),
    ("Apply to 1 and 3 please", [1, 3]),
    ("yes to 2", [2]),
    ("numbers 3 & 7", [3, 7]),
    ("just 10", [10]),
    ("Can you do 4?", [4]),
])
def test_however_someone_phrases_it(reply, expected):
    assert extract_reply_numbers(reply, max_number=10) == expected


@pytest.mark.parametrize("reply", [
    "all", "ALL", "apply to all of them", "all of the above thanks", "every one",
])
def test_all_means_every_job_in_that_digest(reply):
    assert extract_reply_numbers(reply, max_number=4) == [1, 2, 3, 4]


@pytest.mark.parametrize("reply", [
    "none", "none of these", "no thanks", "not interested", "skip them all",
])
def test_a_clear_no_selects_nothing(reply):
    assert extract_reply_numbers(reply, max_number=4) == []


def test_none_wins_over_a_stray_number():
    """'none of the 5 look right' must not select job 5."""
    assert extract_reply_numbers("none of the 5 look right", max_number=10) == []


def test_all_does_not_fire_on_an_unrelated_word():
    assert extract_reply_numbers("finally, 2 please", max_number=5) == [2]
