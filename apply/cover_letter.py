"""Generate tailored cover letters via Gemini with stop-slop filtering."""
import os
import re
import google.generativeai as genai

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
MODEL = "gemini-1.5-flash"

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
    r"it's worth noting",
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
    """Remove AI writing tells from generated text."""
    for pattern, replacement in SLOP_REPLACEMENTS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    for phrase in SLOP_PHRASES:
        # Replace slop phrase sentences with nothing, then clean up whitespace
        text = re.sub(
            rf"[^.!?]*{phrase}[^.!?]*[.!?]\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
    # Clean up extra blank lines
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def generate_cover_letter(
    user_name: str,
    resume_text: str,
    job_title: str,
    company: str,
    job_description: str,
) -> str:
    system = f"""You are writing a cover letter for {user_name}.
Their resume:
{resume_text[:2000]}

Rules:
- Exactly 3 short paragraphs, under 250 words total
- No salutation (no "Dear Hiring Manager")
- Start immediately with a strong, specific first sentence about the role
- No clichés, no filler phrases, no sycophantic openers
- Be direct and confident
- Use concrete examples from the resume where possible
- Do not use: delve, tapestry, leverage (metaphorically), synergy, passionate, excited, thrilled, spearhead, dynamic, robust, seamless
- Sound like a smart human wrote this in 20 minutes, not an AI"""

    prompt = f"""Write a cover letter for this position:

Job Title: {job_title}
Company: {company}
Description: {job_description[:1500]}"""

    model = genai.GenerativeModel(MODEL, system_instruction=system)
    msg = model.generate_content(prompt)
    raw = msg.text.strip()
    return stop_slop(raw)
