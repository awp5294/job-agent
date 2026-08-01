"""Gmail API integration — send digests, poll for replies."""
import os
import re
import json
import base64
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

REDIRECT_URI = os.getenv("BASE_URL", "http://localhost:8000") + "/auth/gmail/callback"


def get_oauth_flow():
    client_config = {
        "web": {
            "client_id": os.getenv("GMAIL_CLIENT_ID"),
            "client_secret": os.getenv("GMAIL_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = REDIRECT_URI
    return flow


def get_gmail_service(credentials_json: str):
    creds_data = json.loads(credentials_json)
    creds = Credentials(
        token=creds_data.get("token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GMAIL_CLIENT_ID"),
        client_secret=os.getenv("GMAIL_CLIENT_SECRET"),
        scopes=SCOPES,
    )
    return build("gmail", "v1", credentials=creds)


def send_digest_email(
    to_email: str,
    user_name: str,
    jobs: list[dict],
    credentials_json: str = None,
) -> str | None:
    """Send the job digest. Returns thread_id for reply tracking."""
    subject = f"Your job digest — {len(jobs)} matches · {datetime.now().strftime('%b %d')}"
    html = _build_digest_html(user_name, jobs)
    text = _build_digest_text(user_name, jobs)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["To"] = to_email
    msg["From"] = os.getenv("SMTP_USER", "job-agent@noreply.com")
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    if credentials_json:
        try:
            service = get_gmail_service(credentials_json)
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            result = service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()
            return result.get("threadId")
        except Exception as e:
            print(f"[gmail] send error: {e}")

    # SMTP fallback
    try:
        with smtplib.SMTP(
            os.getenv("SMTP_HOST", "smtp.gmail.com"),
            int(os.getenv("SMTP_PORT", "587")),
        ) as smtp:
            smtp.starttls()
            smtp.login(os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASS", ""))
            smtp.sendmail(msg["From"], to_email, msg.as_string())
    except Exception as e:
        print(f"[smtp] send error: {e}")

    return None


def poll_for_replies(credentials_json: str, user_email: str) -> list[int]:
    """
    Polls Gmail for replies to job digest emails.
    Returns list of job numbers the user mentioned.
    """
    try:
        service = get_gmail_service(credentials_json)
        results = service.users().messages().list(
            userId="me",
            q=f'to:me from:{user_email} subject:"job digest" newer_than:7d',
            maxResults=20,
        ).execute()
        messages = results.get("messages", [])
        numbers = []
        for msg_ref in messages:
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="full"
            ).execute()
            snippet = msg.get("snippet", "")
            found = re.findall(r"\b\d+\b", snippet)
            numbers.extend(int(n) for n in found if 1 <= int(n) <= 50)
        return list(set(numbers))
    except Exception as e:
        print(f"[gmail] poll error: {e}")
        return []


def _build_digest_html(user_name: str, jobs: list[dict]) -> str:
    items = ""
    for i, j in enumerate(jobs, 1):
        salary = ""
        if j.get("salary_min"):
            salary = f"${j['salary_min']:,}"
            if j.get("salary_max"):
                salary += f"–${j['salary_max']:,}"
        domain = j.get("company_domain", "")
        logo = f'<img src="https://logo.clearbit.com/{domain}" width="20" height="20" style="border-radius:4px;vertical-align:middle;margin-right:6px" onerror="this.style.display=\'none\'">' if domain else ""
        items += f"""
        <tr>
          <td style="padding:16px;border-bottom:1px solid #f0f0f0">
            <strong style="font-size:15px">{i}. {j['title']}</strong><br>
            <span style="color:#444">{logo}{j['company']}</span>
            <span style="color:#888;margin-left:8px">· {j.get('location','')}</span>
            {f'<span style="color:#888;margin-left:8px">· {salary}</span>' if salary else ''}
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
    <p style="margin:8px 0 0;color:#888;font-size:12px">The agent will open each application, pre-fill the form, and generate a tailored cover letter.</p>
  </div>
</body></html>"""


def _build_digest_text(user_name: str, jobs: list[dict]) -> str:
    lines = [f"Hi {user_name} — {len(jobs)} job matches today\n"]
    for i, j in enumerate(jobs, 1):
        lines.append(f"{i}. {j['title']} @ {j['company']} ({j.get('location','')}) — {j.get('score',0)}% match")
        lines.append(f"   {j.get('score_reason','')}")
        lines.append(f"   {j.get('apply_url','')}\n")
    lines.append("Reply with job numbers to apply, e.g.: 1, 3, 5")
    return "\n".join(lines)
