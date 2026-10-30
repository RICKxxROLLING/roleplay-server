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
    # A second factor without a first one means nothing; the server is open.
    totp = db.get(AppSetting, "auth_totp")
    if totp is not None:
        db.delete(totp)
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


def revoke_all_except(db: DbSession, keep: str | None) -> None:
    """Sign out every other device, e.g. when two-factor is turned on."""
    keep_hash = _fingerprint(keep) if keep else None
    db.query(AuthToken).filter(AuthToken.token_hash != keep_hash).delete()
    db.commit()


# --- two-factor (TOTP, RFC 6238) ---------------------------------------------
# Standard-library only, like the password hashing: HMAC-SHA1, 30-second steps,
# six digits -- what every authenticator app (Google, Aegis, 1Password, Authy)
# speaks by default. Stored as one AppSetting row:
#   {"secret": base32, "enabled": bool, "last_step": int, "recovery": [sha256...]}
# `enabled` stays false between setup and the first correct code, so a setup
# abandoned half way never locks the owner out.

TOTP_KEY = "auth_totp"
TOTP_STEP = 30
TOTP_DIGITS = 6
#: Steps either side of now that still count. One covers a phone clock ~30 s off.
TOTP_WINDOW = 1
RECOVERY_CODES = 8


def _totp_row(db: DbSession) -> dict:
    row = db.get(AppSetting, TOTP_KEY)
    return dict(row.value) if row and isinstance(row.value, dict) else {}


def _save_totp(db: DbSession, value: dict | None) -> None:
    row = db.get(AppSetting, TOTP_KEY)
    if value is None:
        if row is not None:
            db.delete(row)
    elif row is None:
        db.add(AppSetting(key=TOTP_KEY, value=value))
    else:
        row.value = value
    db.commit()


def totp_enabled(db: DbSession) -> bool:
    return bool(_totp_row(db).get("enabled"))


def totp_code(secret: str, step: int) -> str:
    import base64
    import struct

    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**TOTP_DIGITS).zfill(TOTP_DIGITS)


def _step(at: float | None = None) -> int:
    import time

    return int((time.time() if at is None else at) // TOTP_STEP)


def begin_totp(db: DbSession, account: str = "owner") -> tuple[str, str]:
    """A fresh secret, not yet enforced. Returns (secret, otpauth URI)."""
    import base64
    from urllib.parse import quote

    secret = base64.b32encode(os.urandom(20)).decode().rstrip("=")
    _save_totp(db, {"secret": secret, "enabled": False, "last_step": 0, "recovery": []})
    uri = (f"otpauth://totp/Roleplay:{quote(account)}?secret={secret}"
           f"&issuer=Roleplay&digits={TOTP_DIGITS}&period={TOTP_STEP}")
    return secret, uri


def _normalise(code: str) -> str:
    return "".join(ch for ch in (code or "") if ch.isalnum()).lower()


def _check_code(state: dict, code: str, at: float | None = None) -> int | None:
    """The step a code matches, or None. Rejects a step already used, so a code
    seen over someone's shoulder can't be replayed inside its 30 seconds."""
    code = _normalise(code)
    if not state.get("secret") or len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    now = _step(at)
    for step in range(now - TOTP_WINDOW, now + TOTP_WINDOW + 1):
        if step > state.get("last_step", 0) and hmac.compare_digest(
            totp_code(state["secret"], step), code
        ):
            return step
    return None


def enable_totp(db: DbSession, code: str, at: float | None = None) -> list[str] | None:
    """Turn two-factor on once the app shows a correct code. Returns the
    recovery codes -- shown once, stored only as hashes -- or None."""
    state = _totp_row(db)
    step = _check_code(state, code, at)
    if step is None:
        return None
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(RECOVERY_CODES)]
    state.update(enabled=True, last_step=step,
                 recovery=[_fingerprint(_normalise(c)) for c in codes])
    _save_totp(db, state)
    return codes


def verify_second_factor(db: DbSession, code: str, at: float | None = None) -> bool:
    """A current authenticator code, or an unused recovery code (spent on use)."""
    state = _totp_row(db)
    if not state.get("enabled"):
        return True
    step = _check_code(state, code, at)
    if step is not None:
        state["last_step"] = step
        _save_totp(db, state)
        return True
    fp = _fingerprint(_normalise(code))
    if fp in state.get("recovery", []):
        state["recovery"] = [r for r in state["recovery"] if r != fp]
        _save_totp(db, state)
        return True
    return False


def recovery_left(db: DbSession) -> int:
    return len(_totp_row(db).get("recovery", []))


def disable_totp(db: DbSession) -> None:
    _save_totp(db, None)
