"""Encrypt small secrets before they go in the database.

Users hand the app their own AI API keys. Those keys spend real money, and the
database is visible in Replit's Database tool to anyone with access to the
project, so they can't sit there in plain text. Each one is sealed with a key
derived from SECRET_KEY and unsealed only at the moment it's used.

If SECRET_KEY changes, every sealed value becomes unreadable. That surfaces as
SecretBoxError, which callers turn into "add your key again in Settings".
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


class SecretBoxError(RuntimeError):
    """The value could not be unsealed. Almost always: SECRET_KEY changed."""


def _fernet() -> Fernet:
    secret = os.environ.get("SECRET_KEY", "change-me-in-production")
    # Fernet wants exactly 32 url-safe base64 bytes; SECRET_KEY is free text.
    digest = hashlib.sha256(f"secretbox:{secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def seal(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def unseal(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise SecretBoxError(
            "A saved secret can't be read any more. This happens when the "
            "server's SECRET_KEY changes; the value has to be entered again."
        ) from exc
