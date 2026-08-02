"""Rendering the emails the agent sends: the daily digest and the
cover-letter follow-up.

Pure formatting — sending lives in mailbox.py.
"""
from datetime import datetime


def digest_subject(jobs: list) -> str:
    return f"Your job digest — {len(jobs)} matches · {datetime.now().strftime('%b %d')}"


def application_subject(items: list) -> str:
    return f"Ready to apply — {len(items)} cover letter" + ("s" if len(items) != 1 else "")


def build_application_html(user_name: str, items: list[dict]) -> str:
    blocks = ""
    for item in items:
        letter = (item.get("cover_letter") or "").strip()
        body = (
            f'<pre style="white-space:pre-wrap;font-family:inherit;font-size:14px;'
            f'background:#fafafa;border:1px solid #eee;border-radius:8px;padding:14px;'
            f'margin:12px 0">{_escape(letter)}</pre>'
            if letter else
            f'<p style="color:#b45309;margin:12px 0">No cover letter — '
            f'{_escape(item.get("note") or "generation failed")}. '
            f'You can retry from your dashboard.</p>'
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
    <h2 style="color:white;margin:0">Your cover letters are ready</h2>
    <p style="color:#c7d2fe;margin:4px 0 0">Hi {_escape(user_name)} — {len(items)} job(s) from your reply</p>
  </div>
  <div style="padding:0 20px">{blocks}</div>
  <div style="padding:20px;background:#f9f9f9;border-radius:0 0 12px 12px;color:#555;font-size:13px">
    Copy the letter, tap the button, and paste it into the form. Everything is
    also on your dashboard if you'd rather edit it there first.
  </div>
</body></html>"""


def build_application_text(user_name: str, items: list[dict]) -> str:
    lines = [f"Hi {user_name} — cover letters for the {len(items)} job(s) you picked.\n"]
    for item in items:
        lines.append(f"{item.get('title','')} @ {item.get('company','')}")
        lines.append(f"Apply: {item.get('apply_url','')}\n")
        letter = (item.get("cover_letter") or "").strip()
        lines.append(letter if letter else
                     f"(No cover letter — {item.get('note') or 'generation failed'})")
        lines.append("\n" + "-" * 60 + "\n")
    return "\n".join(lines)


def _escape(value: str) -> str:
    """Job titles and companies come from scraped pages."""
    return (
        str(value or "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_digest_html(user_name: str, jobs: list[dict]) -> str:
    items = ""
    for i, j in enumerate(jobs, 1):
        salary = ""
        if j.get("salary_min"):
            salary = f"${j['salary_min']:,}"
            if j.get("salary_max"):
                salary += f"–${j['salary_max']:,}"
        salary_html = (
            f'<span style="color:#888;margin-left:8px">· {salary}</span>' if salary else ""
        )
        items += f"""
        <tr>
          <td style="padding:16px;border-bottom:1px solid #f0f0f0">
            <strong style="font-size:15px">{i}. {j.get('title','')}</strong><br>
            <span style="color:#444">{j.get('company','')}</span>
            <span style="color:#888;margin-left:8px">· {j.get('location','')}</span>
            {salary_html}
            <span style="background:#e8f5e9;color:#2e7d32;padding:2px 8px;border-radius:10px;font-size:12px;margin-left:8px">{j.get('score',0)}% match</span><br>
            <span style="color:#666;font-size:13px;margin-top:4px;display:block">{j.get('score_reason','')}</span>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;color:#222">
  <div style="background:#6366f1;padding:24px;border-radius:12px 12px 0 0">
    <h2 style="color:white;margin:0">Your job digest</h2>
    <p style="color:#c7d2fe;margin:4px 0 0">Hi {user_name} — {len(jobs)} matches found today</p>
  </div>
  <table style="width:100%;border-collapse:collapse">{items}</table>
  <div style="padding:20px;background:#f9f9f9;border-radius:0 0 12px 12px">
    <p style="margin:0;color:#555">Reply to this email with the job numbers you want to apply to, e.g. <strong>1, 3, 5</strong></p>
    <p style="margin:8px 0 0;color:#888;font-size:12px">Those jobs move to Selected on your dashboard, where the agent writes a tailored cover letter for each.</p>
  </div>
</body></html>"""


def build_application_html(user_name: str, items: list[dict]) -> str:
    blocks = ""
    for item in items:
        letter = (item.get("cover_letter") or "").strip()
        body = (
            f'<pre style="white-space:pre-wrap;font-family:inherit;font-size:14px;'
            f'background:#fafafa;border:1px solid #eee;border-radius:8px;padding:14px;'
            f'margin:12px 0">{_escape(letter)}</pre>'
            if letter else
            f'<p style="color:#b45309;margin:12px 0">No cover letter — '
            f'{_escape(item.get("note") or "generation failed")}. '
            f'You can retry from your dashboard.</p>'
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
    <h2 style="color:white;margin:0">Your cover letters are ready</h2>
    <p style="color:#c7d2fe;margin:4px 0 0">Hi {_escape(user_name)} — {len(items)} job(s) from your reply</p>
  </div>
  <div style="padding:0 20px">{blocks}</div>
  <div style="padding:20px;background:#f9f9f9;border-radius:0 0 12px 12px;color:#555;font-size:13px">
    Copy the letter, tap the button, and paste it into the form. Everything is
    also on your dashboard if you'd rather edit it there first.
  </div>
</body></html>"""


def build_application_text(user_name: str, items: list[dict]) -> str:
    lines = [f"Hi {user_name} — cover letters for the {len(items)} job(s) you picked.\n"]
    for item in items:
        lines.append(f"{item.get('title','')} @ {item.get('company','')}")
        lines.append(f"Apply: {item.get('apply_url','')}\n")
        letter = (item.get("cover_letter") or "").strip()
        lines.append(letter if letter else
                     f"(No cover letter — {item.get('note') or 'generation failed'})")
        lines.append("\n" + "-" * 60 + "\n")
    return "\n".join(lines)


def _escape(value: str) -> str:
    """Job titles and companies come from scraped pages."""
    return (
        str(value or "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_digest_text(user_name: str, jobs: list[dict]) -> str:
    lines = [f"Hi {user_name} — {len(jobs)} job matches today\n"]
    for i, j in enumerate(jobs, 1):
        lines.append(
            f"{i}. {j.get('title','')} @ {j.get('company','')} "
            f"({j.get('location','')}) — {j.get('score',0)}% match"
        )
        lines.append(f"   {j.get('score_reason','')}")
        lines.append(f"   {j.get('apply_url','')}\n")
    lines.append("Reply with job numbers to apply, e.g.: 1, 3, 5")
    return "\n".join(lines)
