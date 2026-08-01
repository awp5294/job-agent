"""Generate tailored cover letters with Claude, then strip AI writing tells."""
import re

from llm import complete_text

SLOP_REPLACEMENTS = {
    r"\bdelve\b": "explore",
    r"\btapestry\b": "mix",
    r"\bnuanced\b": "detailed",
    r"\bleverage\b": "use",
    r"\bsynergy\b": "collaboration",
    r"\bspearhead\b": "lead",
    r"\bdynamic\b": "strong",
    r"\bsupercharge\b": "improve",
    r"\brobust\b": "strong",
    r"\bseamless\b": "smooth",
    r"\bparadigm\b": "approach",
    r"\bholistic\b": "comprehensive",
}

SLOP_PHRASES = [
    r"I am (deeply |truly |genuinely )?passionate about",
    r"I am (deeply |truly |genuinely )?excited (to|about)",
    r"I would be (thrilled|honored|delighted) to",
    r"it'?s worth noting",
    r"it is worth noting",
    r"I believe that I",
    r"Throughout my career",
    r"As someone who",
    r"I am writing to express",
    r"Please find attached",
    r"I am a (highly |deeply )?motivated",
    r"results-driven",
    r"team player",
    r"go-getter",
    r"think outside the box",
]


def stop_slop(text: str) -> str:
    """Remove AI writing tells. Word swaps first, then whole offending sentences."""
    for pattern, replacement in SLOP_REPLACEMENTS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    for phrase in SLOP_PHRASES:
        text = re.sub(rf"[^.!?]*{phrase}[^.!?]*[.!?]\s*", "", text, flags=re.IGNORECASE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def generate_cover_letter(
    job_title: str,
    company: str,
    job_description: str,
    resume_text: str = "",
    criteria: dict | None = None,
) -> str:
    system = f"""You are writing a cover letter for a job applicant.

Their resume:
{(resume_text or '(no resume provided)')[:2000]}

Rules:
- Exactly 3 short paragraphs, under 250 words total
- No salutation (no "Dear Hiring Manager") and no sign-off
- Start immediately with a specific first sentence about the role
- No cliches, no filler, no sycophantic openers
- Be direct and confident
- Use concrete details from the resume wherever you can
- Never use: delve, tapestry, leverage (metaphorically), synergy, passionate,
  excited, thrilled, spearhead, dynamic, robust, seamless
- Sound like a smart human wrote it in 20 minutes, not like an AI
- Output only the letter itself, with no preamble or commentary"""

    prompt = f"""Write a cover letter for this position:

Job Title: {job_title}
Company: {company}
Description: {(job_description or '')[:1500]}"""

    return stop_slop(complete_text(system=system, prompt=prompt, max_tokens=4000))
