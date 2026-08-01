"""Gmail integration — OAuth, sending the digest, and reading replies."""
import base64
import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def oauth_configured() -> bool:
    return bool(os.getenv("GMAIL_CLIENT_ID") and os.getenv("GMAIL_CLIENT_SECRET"))


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_USER") and os.getenv("SMTP_PASS"))


def get_oauth_flow(redirect_uri: str, state: str = "") -> Flow:
    client_config = {
        "web": {
            "client_id": os.getenv("GMAIL_CLIENT_ID"),
            "client_secret": os.getenv("GMAIL_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES, state=state or None)
    flow.redirect_uri = redirect_uri
    return flow


def get_gmail_service(creds_data: dict):
    """Build a Gmail client from the stored credential dict."""
    creds = Credentials(
        token=creds_data.get("token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri=creds_data.get("token_uri") or "https://oauth2.googleapis.com/token",
        client_id=creds_data.get("client_id") or os.getenv("GMAIL_CLIENT_ID"),
        client_secret=creds_data.get("client_secret") or os.getenv("GMAIL_CLIENT_SECRET"),
        scopes=creds_data.get("scopes") or SCOPES,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def get_verified_email(service) -> str:
    """The Google-verified address for the connected mailbox.

    This is the identity anchor for sign-in: it comes from Google, not from
    anything the person typed into the onboarding chat.
    """
    profile = service.users().getProfile(userId="me").execute()
    return (profile.get("emailAddress") or "").lower()


# ── Sending ────────────────────────────────────────────────────────────────

def build_digest_message(to_email: str, user_name: str, jobs: list[dict]) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"Your job digest — {len(jobs)} matches · {datetime.now().strftime('%b %d')}"
    )
    msg["To"] = to_email
    msg["From"] = os.getenv("SMTP_USER") or to_email
    msg.attach(MIMEText(_build_digest_text(user_name, jobs), "plain"))
    msg.attach(MIMEText(_build_digest_html(user_name, jobs), "html"))
    return msg


def build_application_message(to_email: str, user_name: str,
                              items: list[dict]) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Ready to apply — {len(items)} cover letter" + \
        ("s" if len(items) != 1 else "")
    msg["To"] = to_email
    msg["From"] = os.getenv("SMTP_USER") or to_email
    msg.attach(MIMEText(_build_application_text(user_name, items), "plain"))
    msg.attach(MIMEText(_build_application_html(user_name, items), "html"))
    return msg


def send_application_email(service, to_email: str, user_name: str,
                           items: list[dict]) -> str | None:
    """Send the cover letters for the jobs the user picked, with apply links."""
    msg = build_application_message(to_email, user_name, items)

    if service is not None:
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return result.get("threadId")

    if not smtp_configured():
        raise RuntimeError("No way to send email: connect Gmail, or set SMTP_USER/SMTP_PASS.")

    with smtplib.SMTP(
        os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "587"))
    ) as smtp:
        smtp.starttls()
        smtp.login(os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASS", ""))
        smtp.sendmail(msg["From"], to_email, msg.as_string())
    return None


def send_digest_email(service, to_email: str, user_name: str,
                      jobs: list[dict]) -> str | None:
    """Send the digest. Jobs are numbered in the order given.

    Uses the Gmail API when `service` is available, otherwise falls back to SMTP.
    Raises if neither path works, so the caller can record a real failure.
    """
    msg = build_digest_message(to_email, user_name, jobs)

    if service is not None:
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return result.get("threadId")

    if not smtp_configured():
        raise RuntimeError(
            "No way to send email: connect Gmail, or set SMTP_USER and SMTP_PASS."
        )

    with smtplib.SMTP(
        os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "587"))
    ) as smtp:
        smtp.starttls()
        smtp.login(os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASS", ""))
        smtp.sendmail(msg["From"], to_email, msg.as_string())
    return None


# ── Reading replies ────────────────────────────────────────────────────────

QUOTE_MARKER = re.compile(r"^(>|On .+ wrote:|-{2,} ?Original Message)", re.MULTILINE)


def _decode_part(part: dict) -> str:
    data = part.get("body", {}).get("data")
    if not data:
        return ""
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="ignore")


def _plain_body(payload: dict) -> str:
    """Depth-first search for the first text/plain part."""
    if payload.get("mimeType") == "text/plain":
        return _decode_part(payload)
    for part in payload.get("parts", []) or []:
        text = _plain_body(part)
        if text:
            return text
    return ""


def extract_reply_numbers(body: str, max_number: int) -> list[int]:
    """Pull the job numbers out of a reply.

    Only the text above the quoted original counts — otherwise every number in
    the digest we just sent would be read back as a selection.
    """
    match = QUOTE_MARKER.search(body)
    if match:
        body = body[: match.start()]
    numbers = {int(n) for n in re.findall(r"\b\d{1,3}\b", body)}
    return sorted(n for n in numbers if 1 <= n <= max_number)


def poll_for_replies(service, user_email: str, max_number: int = 50) -> list[int]:
    """Return the job numbers the user replied with, newest digest thread first."""
    results = service.users().messages().list(
        userId="me",
        q='from:me subject:"job digest" newer_than:7d',
        maxResults=20,
    ).execute()

    numbers: set[int] = set()
    for ref in results.get("messages", []):
        msg = service.users().messages().get(
            userId="me", id=ref["id"], format="full"
        ).execute()
        body = _plain_body(msg.get("payload", {})) or msg.get("snippet", "")
        numbers.update(extract_reply_numbers(body, max_number))
    return sorted(numbers)


# ── Rendering ──────────────────────────────────────────────────────────────

def _build_digest_html(user_name: str, jobs: list[dict]) -> str:
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


def _build_application_html(user_name: str, items: list[dict]) -> str:
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


def _build_application_text(user_name: str, items: list[dict]) -> str:
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


def _build_digest_text(user_name: str, jobs: list[dict]) -> str:
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
