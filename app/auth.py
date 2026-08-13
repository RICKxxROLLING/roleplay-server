"""Single-password gate for the whole API.

Scope is deliberately small. This project is single-user by design, so there
are no accounts, roles or registration -- one password, one session cookie, and
everything under /api refuses to answer without it. The point is that reaching
the port is no longer the same as having the chats.

Two decisions worth keeping:

**Off until a password is set.** Existing installs keep working after an
upgrade, and nobody is locked out of their own data by a version bump. The UI
nags until one is set, which is the discoverable half of that trade.

**Opaque random tokens stored hashed, not signed cookies.** A signed cookie
needs a secret to survive restarts, which means another thing to generate,
store and rotate. A random token is its own secret, is revocable by deleting a
row, and if the database leaks the stored hashes cannot be replayed.

Password hashing is `hashlib.scrypt` from the standard library rather than
bcrypt or argon2 -- adding a dependency for one call, in a project that
deliberately has almost none, is not a trade worth making.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import secrets

from sqlalchemy.orm import Session as DbSession

from .db.models import AppSetting, AuthToken

#: Where the password hash lives. Not in `settings_store.PERSISTABLE`, so it is
#: never loaded into the settings object and never returned by GET /settings.
PASSWORD_KEY = "auth_password_hash"

COOKIE = "rp_session"
SESSION_DAYS = 30

# ~16MB of memory per attempt. Enough to make offline guessing expensive
# without making a login noticeably slow on a NAS.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --- password ---------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    # Constant time: a timing signal here would leak the hash prefix by prefix.
    return hmac.compare_digest(candidate.hex(), digest_hex)


def get_password_hash(db: DbSession) -> str | None:
    row = db.get(AppSetting, PASSWORD_KEY)
    return row.value if row else None


def is_enabled(db: DbSession) -> bool:
    """Whether a password has been set. No password means no gate."""
    return bool(get_password_hash(db))


def set_password(db: DbSession, password: str) -> None:
    row = db.get(AppSetting, PASSWORD_KEY)
    if row is None:
        db.add(AppSetting(key=PASSWORD_KEY, value=hash_password(password)))
    else:
        row.value = hash_password(password)
    # Changing the password logs every device out; that is the whole point of
    # changing it after one might have been compromised.
    db.query(AuthToken).delete()
    db.commit()


def clear_password(db: DbSession) -> None:
    row = db.get(AppSetting, PASSWORD_KEY)
    if row is not None:
        db.delete(row)
    db.query(AuthToken).delete()
    db.commit()


# --- sessions ---------------------------------------------------------------


def _fingerprint(token: str) -> str:
    """Tokens are stored hashed so a database leak cannot be replayed.

    Plain SHA-256 rather than scrypt: the token is 256 bits of randomness
    already, so there is no low-entropy secret for an attacker to grind.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(db: DbSession) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        AuthToken(
            token_hash=_fingerprint(token),
            expires_at=_now() + dt.timedelta(days=SESSION_DAYS),
        )
    )
    db.commit()
    return token


def valid_token(db: DbSession, token: str | None) -> bool:
    if not token:
        return False
    row = db.get(AuthToken, _fingerprint(token))
    if row is None:
        return False
    expires = row.expires_at
    # SQLite hands back naive datetimes; compare like with like.
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.timezone.utc)
    if expires < _now():
        db.delete(row)
        db.commit()
        return False
    return True


def revoke_token(db: DbSession, token: str | None) -> None:
    if not token:
        return
    row = db.get(AuthToken, _fingerprint(token))
    if row is not None:
        db.delete(row)
        db.commit()


def purge_expired(db: DbSession) -> int:
    rows = db.query(AuthToken).filter(AuthToken.expires_at < _now()).all()
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)
