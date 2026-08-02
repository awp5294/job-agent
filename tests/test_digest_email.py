"""What the digest email actually looks like, and how replies to it are read."""
import pytest

from email_handler.digest import (
    application_subject, build_application_html, build_application_text,
    build_digest_html, build_digest_text, digest_subject, plural, summarise,
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
    assert "1. Staff Product Manager, Payments at Anthropic" in text
    assert "San Francisco / Remote · $180,000–$240,000" in text
    assert "payments platform roadmap" in text
    assert "https://boards.greenhouse.io/anthropic/jobs/1" in text
    assert "2. Senior PM, Growth at Figma" in text


LETTERS = [
    {"title": "Staff Product Manager, Payments", "company": "Anthropic",
     "apply_url": "https://boards.greenhouse.io/anthropic/jobs/1",
     "cover_letter": "I led the ledger migration at Acme.", "note": ""},
]


# ── Telling the reader what to do ──────────────────────────────────────────

def test_the_digest_spells_out_all_three_next_steps():
    """Someone reading on a phone should not have to guess what happens."""
    for body in (build_digest_html("Anthony", JOBS), build_digest_text("Anthony", JOBS)):
        assert "Reply to this email with the numbers you want" in body
        assert '"1, 3, 5"' in body                     # how to phrase it
        assert "cover letter for each job you picked" in body   # what happens after
        assert "paste the letter in, and submit" in body.lower()  # their final move
        assert "15 minutes" in body                    # when to expect it


def test_the_digest_says_what_to_do_if_nothing_appeals():
    for body in (build_digest_html("Anthony", JOBS), build_digest_text("Anthony", JOBS)):
        assert '"none"' in body


def test_the_cover_letter_email_spells_out_its_steps():
    for body in (build_application_html("Anthony", LETTERS),
                 build_application_text("Anthony", LETTERS)):
        assert "Copy the cover letter" in body
        assert "attach your resume" in body
        assert "dashboard" in body


def test_subjects_count_correctly_and_read_naturally():
    assert digest_subject(JOBS).startswith("2 job matches for you")
    assert digest_subject([JOBS[0]]).startswith("1 job match for you")
    assert application_subject(LETTERS) == "Your cover letter is ready"
    assert application_subject(LETTERS * 3) == "Your 3 cover letters are ready"


@pytest.mark.parametrize("count,word,expected", [
    (1, "job match", "1 job match"),
    (3, "job match", "3 job matches"),
    (1, "cover letter", "1 cover letter"),
    (2, "cover letter", "2 cover letters"),
    (2, "box", "2 boxes"),
])
def test_plural_handles_words_that_need_es(count, word, expected):
    assert plural(count, word) == expected


# ── Keeping the copy human ─────────────────────────────────────────────────

SLOP = [
    "seamless", "leverage", "delve", "robust", "elevate", "unlock",
    "empower", "streamline", "supercharge", "effortlessly", "curated",
    "journey", "in today's", "we're excited", "simply", "just a few clicks",
]


@pytest.mark.parametrize("phrase", SLOP)
def test_the_emails_avoid_marketing_slop(phrase):
    bodies = [
        build_digest_html("Anthony", JOBS), build_digest_text("Anthony", JOBS),
        build_application_html("Anthony", LETTERS),
        build_application_text("Anthony", LETTERS),
    ]
    for body in bodies:
        assert phrase not in body.lower(), f"{phrase!r} crept into the email copy"


def test_no_em_dashes_in_the_emails():
    """They read as AI-written, and mangle in some plain-text clients."""
    for body in (build_digest_text("Anthony", JOBS),
                 build_application_text("Anthony", LETTERS),
                 digest_subject(JOBS), application_subject(LETTERS)):
        assert "—" not in body


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
