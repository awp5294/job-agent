"""Cover letters that don't read like a machine wrote them.

Three layers, in order of how much work they do:

1. The prompt. Most of the job. Tell the model the specific patterns to avoid
   rather than handing it a banned-word list.
2. Detection. Scan the draft for the tells that survived and name them.
3. One revision pass, quoting those tells back at the model.

What this deliberately does NOT do is edit the letter with regexes. The old
version deleted any sentence containing a stock phrase, which threw away
"Throughout my career I shipped 12 products at Stripe" along with the cliche,
and rewrote "dynamic pricing engine" as "strong pricing engine". These letters
go to employers, so a mangled sentence is worse than a slightly stiff one.
"""
import re

from llm import LLMError, complete_text

MAX_REVISIONS = 1

# ── What we look for ───────────────────────────────────────────────────────
# Each entry is (pattern, what to tell the model about it).

STOCK_OPENERS = [
    (r"\bI am writing (to|in)\b", 'the opener "I am writing to..."'),
    (r"\bI am (excited|thrilled|delighted|eager)\b", 'an "I am excited/thrilled" opener'),
    (r"\bI am (deeply |truly |genuinely )?passionate\b", '"I am passionate about"'),
    (r"\bAs (a|an|someone who)\b[^.!?]{0,60}(with|who has)\s+\d+\+?\s*years",
     'the "As a X with N years of experience" opener'),
    (r"\bWith (over |more than )?\d+\+?\s*years of (experience|expertise)\b",
     'the "With N years of experience" opener'),
    (r"\bThroughout my career\b", '"Throughout my career"'),
    (r"\bI am a (highly |deeply |self-)?motivated\b", '"I am a motivated..."'),
]

STOCK_CLOSERS = [
    (r"\bI would (welcome|love|relish) the (opportunity|chance)\b",
     '"I would welcome the opportunity"'),
    (r"\bI look forward to (hearing|discussing|the opportunity)\b",
     '"I look forward to hearing from you"'),
    (r"\bThank you for (considering|your time|taking the time)\b",
     '"Thank you for considering my application"'),
    (r"\bI (am confident|believe) that I (would|could|can)\b",
     '"I am confident that I would..."'),
    (r"\bPlease (find attached|do not hesitate)\b", '"Please do not hesitate to..."'),
]

BUZZWORDS = [
    (r"\bleverag(e|ing|ed)\b", "leverage"),
    (r"\bsynerg(y|ies|istic)\b", "synergy"),
    (r"\bspearhead(ed|ing)?\b", "spearhead"),
    (r"\bdelv(e|ing|ed)\b", "delve"),
    (r"\btapestry\b", "tapestry"),
    (r"\bseamless(ly)?\b", "seamless"),
    (r"\bholistic(ally)?\b", "holistic"),
    (r"\bparadigm\b", "paradigm"),
    (r"\bcutting[- ]edge\b", "cutting-edge"),
    (r"\bbest[- ]in[- ]class\b", "best-in-class"),
    (r"\bresults[- ]driven\b", "results-driven"),
    (r"\bteam player\b", "team player"),
    (r"\bgo[- ]getter\b", "go-getter"),
    (r"\bthink outside the box\b", "think outside the box"),
    (r"\bproven track record\b", "proven track record"),
    (r"\bwealth of experience\b", "wealth of experience"),
    (r"\bhit the ground running\b", "hit the ground running"),
    (r"\bwear (many|multiple) hats\b", "wear many hats"),
    (r"\bfast[- ]paced\b", "fast-paced"),
    (r"\bdeep dive\b", "deep dive"),
    (r"\bmove the needle\b", "move the needle"),
    (r"\bgame[- ]chang(er|ing)\b", "game-changer"),
]

# Intensifiers that add nothing. Not every adverb, just the empty ones.
FILLER_ADVERBS = [
    "truly", "deeply", "genuinely", "incredibly", "extremely", "really",
    "very", "highly", "significantly", "substantially", "effectively",
    "successfully", "seamlessly", "particularly", "absolutely",
]

VAGUE_CLAIMS = [
    (r"\bsignificant impact\b", '"significant impact" with no number attached'),
    (r"\bwide (range|variety) of\b", '"a wide range of"'),
    (r"\bnumerous\b", '"numerous" instead of a count'),
    (r"\bvarious\b", '"various" instead of naming them'),
    (r"\bmany different\b", '"many different"'),
]


def find_slop(text: str) -> list[str]:
    """Every AI tell still in the draft, described so the model can fix it."""
    issues: list[str] = []

    for pattern, description in STOCK_OPENERS + STOCK_CLOSERS + VAGUE_CLAIMS:
        if re.search(pattern, text, re.IGNORECASE):
            issues.append(description)

    for pattern, word in BUZZWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            issues.append(f'the word "{word}"')

    found_adverbs = [
        adverb for adverb in FILLER_ADVERBS
        if re.search(rf"\b{adverb}\b", text, re.IGNORECASE)
    ]
    if found_adverbs:
        issues.append("filler adverbs: " + ", ".join(sorted(found_adverbs)))

    if "—" in text or "–" in text:
        issues.append("em dashes, which read as machine-written")

    if re.search(r"\bnot (just |only )?[^.!?,]{3,40}, but\b", text, re.IGNORECASE):
        issues.append('a "not just X, but Y" construction')

    if re.search(r"^\s*(Dear|To whom)", text, re.IGNORECASE):
        issues.append("a salutation, which was not asked for")

    return issues


# ── Mechanical cleanup ─────────────────────────────────────────────────────
# Only changes that cannot damage meaning.

SIGN_OFFS = re.compile(
    r"\n+\s*(Sincerely|Best regards|Kind regards|Warm regards|Regards|"
    r"Best|Yours (sincerely|faithfully|truly)|Thanks|Thank you)\s*,?\s*"
    r"(\n+.*)?$",
    re.IGNORECASE,
)
SALUTATION = re.compile(r"^\s*(Dear[^\n]*|To whom it may concern[^\n]*)\n+", re.IGNORECASE)


def stop_slop(text: str) -> str:
    """Tidy the draft without rewriting its content.

    Strips wrappers the model was told not to add, normalises punctuation, and
    leaves every sentence the model wrote intact.
    """
    if not text:
        return ""

    # Markdown fences, if the model wrapped its answer.
    text = re.sub(r"^\s*```[a-z]*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)

    text = SALUTATION.sub("", text)
    text = SIGN_OFFS.sub("", text)

    # Em dash to a comma keeps the sentence readable; a spaced one becomes a
    # full stop only when it is clearly joining two clauses.
    text = re.sub(r"\s+—\s+", ", ", text)
    text = text.replace("—", ", ").replace("–", "-")

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Prompts ────────────────────────────────────────────────────────────────

VOICE_RULES = """How to write it:
- Open with the specific thing about this role or company that connects to
  their actual experience. No throat-clearing, no "I am writing to apply".
- Every claim needs a concrete detail from the resume behind it: what they
  built, what changed, what it measured. "Cut settlement time from two days to
  four hours" beats "significant impact on efficiency".
- Active voice. The applicant is the subject of their own sentences.
- Vary sentence length. Two short ones in a row, then a longer one.
- Cut every adverb that isn't doing work: truly, deeply, genuinely,
  significantly, effectively, seamlessly.
- No em dashes. Use a comma or start a new sentence.
- No "not just X, but Y" constructions, no rhetorical questions, no lines
  written to sound quotable.
- Never use: leverage, synergy, spearhead, delve, seamless, holistic,
  cutting-edge, results-driven, team player, proven track record, wealth of
  experience, hit the ground running, fast-paced, passionate, excited, thrilled.
- Don't close by thanking them or saying you look forward to hearing back.
  End on something about the work.
- Sound like a competent person who spent twenty minutes on it, not like a
  template and not like a machine."""


def _system_prompt(resume_text: str) -> str:
    return f"""You write cover letters for a job applicant. Write as them, in
first person.

Their resume:
{(resume_text or '(no resume provided)')[:2000]}

Format: exactly 3 short paragraphs, under 250 words total. No salutation, no
sign-off, no subject line. Output only the letter.

{VOICE_RULES}"""


def _revision_prompt(letter: str, issues: list[str]) -> str:
    listed = "\n".join(f"- {issue}" for issue in issues)
    return f"""This draft still has AI writing tells in it:

{listed}

Rewrite it with those removed. Keep every concrete fact, number and example
exactly as it is; change only the phrasing around them. Same length, same three
paragraphs. Output only the rewritten letter.

DRAFT:
{letter}"""


def generate_cover_letter(
    job_title: str,
    company: str,
    job_description: str,
    resume_text: str = "",
    criteria: dict | None = None,
) -> str:
    """Write a letter, check it for AI tells, and revise once if it has any."""
    system = _system_prompt(resume_text)
    prompt = f"""Write a cover letter for this position:

Job Title: {job_title}
Company: {company}
Description: {(job_description or '')[:1500]}"""

    letter = stop_slop(complete_text(system=system, prompt=prompt, max_tokens=4000))

    for _ in range(MAX_REVISIONS):
        issues = find_slop(letter)
        if not issues:
            break
        try:
            revised = stop_slop(complete_text(
                system=system,
                prompt=_revision_prompt(letter, issues),
                max_tokens=4000,
            ))
        except LLMError as exc:
            # A failed revision is not a failed letter. Keep the draft.
            print(f"[cover_letter] revision pass failed, keeping draft: {exc}")
            break
        # Only take the revision if it actually improved things.
        if len(find_slop(revised)) < len(issues):
            letter = revised
        else:
            break

    return letter
