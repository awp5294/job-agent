"""Password hashing.

Uses scrypt from the standard library, so there's no extra dependency to
install and nothing to keep patched.
"""
import hashlib
import hmac
import secrets

# Cost parameters. n is the expensive one; 2**14 keeps a hash around 50-100ms,
# which is slow enough to make guessing painful and fast enough for a login.
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 8


class PasswordError(ValueError):
    """The password isn't acceptable. The message is shown to the user."""


def check_password_quality(password: str) -> None:
    """Raise PasswordError if this password shouldn't be allowed."""
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise PasswordError(
            f"Please use at least {MIN_PASSWORD_LENGTH} characters."
        )
    if password.strip() != password:
        raise PasswordError("Please don't start or end your password with a space.")


def hash_password(password: str) -> str:
    """Return a self-describing hash string safe to store in the database."""
    check_password_quality(password)
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check of a password against a stored hash."""
    if not password or not stored:
        return False
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest_hex)
