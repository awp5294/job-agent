"""The app's own mailbox: one account that sends every digest and receives
every reply.

This replaces asking each person to connect their own Gmail. You configure one
address; users just tell the app where to send their digest. Replies come back
to this same mailbox and are matched to a user by the address they came from.

Setup is a Gmail address plus an App Password (Google Account -> Security ->
2-Step Verification -> App passwords). No Google Cloud project, no OAuth
consent screen, and nothing for your friends to approve.
"""
import email
import imaplib
import os
import re
import smtplib
from email.header import decode_header, make_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr


class MailboxError(RuntimeError):
    """Sending or receiving failed. The message is safe to show a user."""


def smtp_settings() -> dict:
    return {
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASS", ""),
        "from": os.getenv("MAIL_FROM") or os.getenv("SMTP_USER", ""),
    }


def imap_settings() -> dict:
    return {
        # Defaults to the SMTP host's IMAP equivalent for Gmail.
        "host": os.getenv("IMAP_HOST", "imap.gmail.com"),
        "port": int(os.getenv("IMAP_PORT", "993")),
        "user": os.getenv("IMAP_USER") or os.getenv("SMTP_USER", ""),
        "password": os.getenv("IMAP_PASS") or os.getenv("SMTP_PASS", ""),
        "folder": os.getenv("IMAP_FOLDER", "INBOX"),
    }


def mailbox_configured() -> bool:
    settings = smtp_settings()
    return bool(settings["user"] and settings["password"])


def mailbox_address() -> str:
    return smtp_settings()["from"]


# ── Sending ────────────────────────────────────────────────────────────────

def build_message(to_email: str, subject: str, text: str, html: str) -> MIMEMultipart:
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["To"] = to_email
    message["From"] = mailbox_address()
    # Replies must land back in this mailbox, since that's the inbox we read.
    message["Reply-To"] = mailbox_address()
    message.attach(MIMEText(text, "plain"))
    message.attach(MIMEText(html, "html"))
    return message


def send_email(to_email: str, subject: str, text: str, html: str) -> None:
    if not mailbox_configured():
        raise MailboxError(
            "No mailbox configured — set SMTP_USER and SMTP_PASS so the app "
            "can send digests."
        )
    settings = smtp_settings()
    message = build_message(to_email, subject, text, html)
    try:
        with smtplib.SMTP(settings["host"], settings["port"], timeout=30) as smtp:
            smtp.starttls()
            smtp.login(settings["user"], settings["password"])
            smtp.sendmail(settings["from"], [to_email], message.as_string())
    except smtplib.SMTPAuthenticationError as exc:
        raise MailboxError(
            "The mail server rejected SMTP_USER/SMTP_PASS. For Gmail this must "
            "be an App Password, not your normal password."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailboxError(f"Could not send email: {exc}") from exc


# ── Receiving ──────────────────────────────────────────────────────────────

QUOTE_MARKER = re.compile(r"^(>|On .+ wrote:|-{2,} ?Original Message)", re.MULTILINE)


def _decode(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _plain_body(message) -> str:
    """First text/plain part, skipping attachments."""
    if message.is_multipart():
        for part in message.walk():
            disposition = str(part.get("Content-Disposition") or "")
            if part.get_content_type() == "text/plain" and "attachment" not in disposition:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="ignore")
        return ""
    payload = message.get_payload(decode=True) or b""
    charset = message.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="ignore")


def strip_quoted(body: str) -> str:
    """Drop the quoted original, so the digest's own numbers aren't read back."""
    match = QUOTE_MARKER.search(body)
    return body[: match.start()] if match else body


def extract_reply_numbers(body: str, max_number: int) -> list[int]:
    """The job numbers someone replied with, bounded by the digest length."""
    numbers = {int(n) for n in re.findall(r"\b\d{1,3}\b", strip_quoted(body))}
    return sorted(n for n in numbers if 1 <= n <= max_number)


def fetch_replies(subject_contains: str = "job digest") -> list[dict]:
    """Unread replies to a digest, as {from_email, subject, body}.

    Messages are marked read once returned, so a reply is only ever acted on
    once even if the poller runs again a minute later.
    """
    if not mailbox_configured():
        return []

    settings = imap_settings()
    replies: list[dict] = []
    try:
        connection = imaplib.IMAP4_SSL(settings["host"], settings["port"])
    except OSError as exc:
        raise MailboxError(f"Could not reach the IMAP server: {exc}") from exc

    try:
        try:
            connection.login(settings["user"], settings["password"])
        except imaplib.IMAP4.error as exc:
            raise MailboxError(
                "The mail server rejected the IMAP login. For Gmail this must "
                "be an App Password, and IMAP must be enabled in Gmail settings."
            ) from exc

        connection.select(settings["folder"])
        status, data = connection.search(None, "UNSEEN")
        if status != "OK":
            return []

        for message_id in (data[0] or b"").split():
            status, fetched = connection.fetch(message_id, "(RFC822)")
            if status != "OK" or not fetched or not fetched[0]:
                continue
            message = email.message_from_bytes(fetched[0][1])

            subject = _decode(message.get("Subject"))
            if subject_contains.lower() not in subject.lower():
                # Not a digest reply — leave it unread for a human to deal with.
                continue

            _, from_email = parseaddr(message.get("From", ""))
            replies.append({
                "from_email": (from_email or "").lower(),
                "subject": subject,
                "body": _plain_body(message),
            })
            connection.store(message_id, "+FLAGS", "\\Seen")
    finally:
        try:
            connection.logout()
        except Exception:
            pass

    return replies
