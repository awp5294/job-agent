"""Rendering the emails the agent sends: the daily digest and the
cover-letter follow-up.

Pure formatting — sending lives in mailbox.py.
"""
import re
from datetime import datetime
from html import unescape


def plural(count: int, word: str) -> str:
    """Count plus the word, pluralised. Handles "job match" -> "job matches"."""
    if count == 1:
        return f"{count} {word}"
    suffix = "es" if word.endswith(("s", "x", "z", "ch", "sh")) else "s"
    return f"{count} {word}{suffix}"


def digest_subject(jobs: list) -> str:
    return f"{plural(len(jobs), 'job match')} for you, {datetime.now().strftime('%b %d')}"


def application_subject(items: list) -> str:
    return f"Your {plural(len(items), 'cover letter')} are ready" if len(items) != 1 \
        else "Your cover letter is ready"


def _escape(value: str) -> str:
    """Titles, companies and locations come from third-party job boards."""
    return (
        str(value or "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _salary_line(job: dict) -> str:
    if not job.get("salary_min"):
        return ""
    salary = f"${job['salary_min']:,}"
    if job.get("salary_max"):
        salary += f"–${job['salary_max']:,}"
    return salary


def _meta_line(job: dict) -> str:
    return " · ".join(p for p in [job.get("location") or "", _salary_line(job)] if p)


def summarise(text: str, limit: int = 220) -> str:
    """A short, readable snippet of a job description.

    Descriptions arrive as HTML from Greenhouse and as plain text elsewhere, so
    strip tags, unescape entities, collapse whitespace, and cut on a word break.
    """
    if not text:
        return ""
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = unescape(plain)
    plain = re.sub(r"\s+", " ", plain)
    # Removing a tag between a word and its punctuation leaves "roadmap ." —
    # close that gap so the snippet doesn't read as broken.
    plain = re.sub(r"\s+([,.;:!?])", r"\1", plain).strip()
    if len(plain) <= limit:
        return plain
    return plain[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


# ── Daily digest ───────────────────────────────────────────────────────────

def build_digest_html(user_name: str, jobs: list[dict]) -> str:
    items = ""
    for i, job in enumerate(jobs, 1):
        meta = _meta_line(job)
        snippet = summarise(job.get("description") or "")
        meta_html = f'<span style="color:#888"> · {_escape(meta)}</span>' if meta else ""
        snippet_html = (
            f'<p style="color:#444;font-size:13px;line-height:1.5;margin:8px 0 10px">'
            f"{_escape(snippet)}</p>" if snippet else ""
        )
        items += f"""
        <tr>
          <td style="padding:18px 16px;border-bottom:1px solid #f0f0f0">
            <div style="font-size:16px;font-weight:600;margin-bottom:3px">{i}. {_escape(job.get('title', ''))}</div>
            <div style="color:#444;font-size:14px">
              <strong>{_escape(job.get('company', ''))}</strong>{meta_html}
              <span style="background:#e8f5e9;color:#2e7d32;padding:2px 8px;border-radius:10px;font-size:12px;margin-left:8px;white-space:nowrap">{job.get('score', 0)}% match</span>
            </div>
            {snippet_html}
            <div style="color:#666;font-size:13px;font-style:italic;margin-bottom:12px">Why: {_escape(job.get('score_reason', ''))}</div>
            <a href="{_escape(job.get('apply_url', ''))}" style="color:#4f46e5;font-size:13px;font-weight:600;text-decoration:none">View the full posting →</a>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:640px;margin:0 auto;color:#222">
  <div style="background:#6366f1;padding:24px;border-radius:12px 12px 0 0">
    <h2 style="color:white;margin:0">{_escape(plural(len(jobs), 'job'))} worth a look</h2>
    <p style="color:#c7d2fe;margin:4px 0 0">Morning {_escape(user_name)}. Here's what came up today.</p>
  </div>
  <table style="width:100%;border-collapse:collapse">{items}</table>
  <div style="padding:20px;background:#f9f9f9;border-radius:0 0 12px 12px">
    <p style="margin:0 0 12px;color:#222;font-size:15px;font-weight:600">What to do next</p>
    <ol style="margin:0;padding-left:20px;color:#444;font-size:14px;line-height:1.7">
      <li><strong>Reply to this email with the numbers you want.</strong>
          "1, 3, 5" works. So does "apply to 1 and 3", or "all".</li>
      <li>The agent writes a cover letter for each job you picked and sends
          them back, usually inside 15 minutes.</li>
      <li>Open the link in that email, paste the letter in, and submit.</li>
    </ol>
    <p style="margin:14px 0 0;color:#888;font-size:13px">
      Nothing for you here? Reply "none", or ignore this. Tomorrow's batch arrives
      at the same time.
    </p>
  </div>
</body></html>"""


def build_digest_text(user_name: str, jobs: list[dict]) -> str:
    lines = [
        f"Morning {user_name}. Here's what came up today: "
        f"{plural(len(jobs), 'job')} worth a look.\n",
    ]
    for i, job in enumerate(jobs, 1):
        meta = _meta_line(job)
        lines.append(
            f"{i}. {job.get('title', '')} at {job.get('company', '')}"
            f"{f' ({meta})' if meta else ''} [{job.get('score', 0)}% match]"
        )
        snippet = summarise(job.get("description") or "", limit=180)
        if snippet:
            lines.append(f"   {snippet}")
        lines.append(f"   Why: {job.get('score_reason', '')}")
        lines.append(f"   {job.get('apply_url', '')}\n")

    lines.append("")
    lines.append("WHAT TO DO NEXT")
    lines.append('1. Reply to this email with the numbers you want. "1, 3, 5" works.')
    lines.append('   So does "apply to 1 and 3", or "all".')
    lines.append("2. The agent writes a cover letter for each job you picked and")
    lines.append("   sends them back, usually inside 15 minutes.")
    lines.append("3. Open the link in that email, paste the letter in, and submit.")
    lines.append("")
    lines.append('Nothing for you here? Reply "none", or ignore this. Tomorrow\'s')
    lines.append("batch arrives at the same time.")
    return "\n".join(lines)


# ── Cover letters, after a reply ───────────────────────────────────────────

def build_application_html(user_name: str, items: list[dict]) -> str:
    blocks = ""
    for item in items:
        letter = (item.get("cover_letter") or "").strip()
        body = (
            f'<pre style="white-space:pre-wrap;font-family:inherit;font-size:14px;'
            f"background:#fafafa;border:1px solid #eee;border-radius:8px;padding:14px;"
            f'margin:12px 0">{_escape(letter)}</pre>'
            if letter else
            f'<p style="color:#b45309;margin:12px 0">No cover letter for this one. '
            f'{_escape(item.get("note") or "Generation failed")}. '
            f"You can retry from your dashboard.</p>"
        )
        blocks += f"""
        <div style="padding:20px 0;border-bottom:1px solid #f0f0f0">
          <strong style="font-size:16px">{_escape(item.get('title', ''))}</strong>
          <span style="color:#666"> · {_escape(item.get('company', ''))}</span>
          {body}
          <a href="{_escape(item.get('apply_url', ''))}"
             style="display:inline-block;background:#6366f1;color:#fff;text-decoration:none;
                    padding:10px 18px;border-radius:8px;font-weight:600">
            Open the application →
          </a>
        </div>"""

    return f"""<!DOCTYPE html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:640px;margin:0 auto;color:#222">
  <div style="background:#6366f1;padding:24px;border-radius:12px 12px 0 0">
    <h2 style="color:white;margin:0">{_escape(application_subject(items))}</h2>
    <p style="color:#c7d2fe;margin:4px 0 0">
      {_escape(user_name)}, you picked {_escape(plural(len(items), 'job'))}. Written and waiting below.
    </p>
  </div>
  <div style="padding:0 20px;background:#fff">
    <div style="padding:18px 0 4px">
      <p style="margin:0 0 10px;color:#222;font-size:15px;font-weight:600">For each job below</p>
      <ol style="margin:0;padding-left:20px;color:#444;font-size:14px;line-height:1.7">
        <li>Copy the cover letter.</li>
        <li>Click <strong>Open the application</strong> to reach the form.</li>
        <li>Paste the letter in, attach your resume, and submit.</li>
      </ol>
    </div>
    {blocks}
  </div>
  <div style="padding:20px;background:#f9f9f9;border-radius:0 0 12px 12px;color:#555;font-size:13px">
    Want to change the wording first? Every letter is on your dashboard, where you
    can edit it before you apply.
  </div>
</body></html>"""


def build_application_text(user_name: str, items: list[dict]) -> str:
    lines = [
        f"{user_name}, you picked {plural(len(items), 'job')}. "
        "Written and waiting below.\n",
        "FOR EACH JOB BELOW",
        "1. Copy the cover letter.",
        "2. Open the apply link.",
        "3. Paste the letter in, attach your resume, and submit.\n",
        "Want to change the wording first? Every letter is on your dashboard.",
        "\n" + "=" * 60 + "\n",
    ]
    for item in items:
        lines.append(f"{item.get('title', '')} at {item.get('company', '')}")
        lines.append(f"Apply: {item.get('apply_url', '')}\n")
        letter = (item.get("cover_letter") or "").strip()
        lines.append(letter if letter else
                     f"(No cover letter. {item.get('note') or 'Generation failed'}. "
                     "You can retry from your dashboard.)")
        lines.append("\n" + "=" * 60 + "\n")
    return "\n".join(lines)
