"""Login, logout, the password, and two-factor.

Login, logout, status and setting the first password are the only endpoints
under /api that answer without a session, so the surface here is the whole
surface. The two-factor routes sit behind the session middleware like
everything else.
"""
from __future__ import annotations

import ipaddress
import json
import time
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from .. import auth
from ..db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

# --- throttling ---------------------------------------------------------------
# Per client, not global. The old single counter meant that once the server was
# public, anyone could fail eight times every five minutes and keep the owner
# out indefinitely. Now each client is locked on its own, for longer each time;
# and a ceiling across every *remote* client still stops a guesser rotating
# addresses -- but never applies to a direct LAN connection, so the owner can
# always sign in from home. Held in memory on purpose: a restart clearing it is
# acceptable, and failed attempts stay out of the database.

#: Failures before a client is locked, and the first lock; it doubles each time.
_CLIENT_FAILURES = 5
_CLIENT_LOCK_SECONDS = 60
_CLIENT_LOCK_MAX = 3600
#: Failures across all remote clients in the window before remote logins pause.
_REMOTE_FAILURES = 30
_REMOTE_WINDOW = 900

_clients: dict[str, dict] = {}
_remote: deque[float] = deque()
_remote_locked_until = 0.0


def _client_key(request: Request) -> str:
    """The visitor's address. Behind Cloudflare every request comes from the
    tunnel container, so its header names the real client. A spoofed header only
    buys a fresh per-client counter -- the remote ceiling still applies."""
    for header in ("cf-connecting-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_remote(request: Request) -> bool:
    """Through a proxy, or from a public address. Direct LAN peers are not."""
    if request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for"):
        return True
    try:
        return not ipaddress.ip_address(request.client.host).is_private
    except (ValueError, AttributeError):
        return True


def _locked(request: Request) -> int:
    now = time.monotonic()
    wait = _clients.get(_client_key(request), {}).get("until", 0.0) - now
    if _is_remote(request):
        wait = max(wait, _remote_locked_until - now)
    return int(wait) + 1 if wait > 0 else 0


def _record_failure(request: Request) -> None:
    global _remote_locked_until
    now = time.monotonic()
    c = _clients.setdefault(_client_key(request), {"fails": 0, "locks": 0, "until": 0.0})
    c["fails"] += 1
    if c["fails"] >= _CLIENT_FAILURES:
        c["until"] = now + min(_CLIENT_LOCK_SECONDS * 2 ** c["locks"], _CLIENT_LOCK_MAX)
        c["locks"] += 1
        c["fails"] = 0
    if _is_remote(request):
        _remote.append(now)
        while _remote and _remote[0] < now - _REMOTE_WINDOW:
            _remote.popleft()
        if len(_remote) >= _REMOTE_FAILURES:
            _remote_locked_until = now + _REMOTE_WINDOW
            _remote.clear()


def _reset_failures(request: Request | None = None) -> None:
    """One client's record after it signs in; everything when `request` is None."""
    global _remote_locked_until
    if request is not None:
        _clients.pop(_client_key(request), None)
        return
    _clients.clear()
    _remote.clear()
    _remote_locked_until = 0.0


def _https(request: Request) -> bool:
    """Whether the browser is on HTTPS -- through Cloudflare it is, even though
    the tunnel talks plain HTTP to us; the proxy headers say so."""
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        return True
    try:
        return json.loads(request.headers.get("cf-visitor", "{}")).get("scheme") == "https"
    except ValueError:
        return False


class LoginIn(BaseModel):
    password: str
    #: From the authenticator app, or a recovery code. Needed once two-factor is on.
    code: str = ""


class PasswordIn(BaseModel):
    password: str
    #: Required once a password exists, so a stolen session cannot silently
    #: change the lock and evict the owner.
    current_password: str | None = None


@router.get("/status")
def status(request: Request, db: DbSession = Depends(get_db)) -> dict:
    """Public by design: the UI has to know whether to show a login screen, and
    whether to ask for a code."""
    enabled = auth.is_enabled(db)
    authenticated = not enabled or auth.valid_token(db, request.cookies.get(auth.COOKIE))
    out = {
        "enabled": enabled,
        "authenticated": authenticated,
        "totp": enabled and auth.totp_enabled(db),
        "locked_for": _locked(request),
    }
    if authenticated and out["totp"]:
        out["recovery_left"] = auth.recovery_left(db)
    return out


@router.post("/login")
def login(
    body: LoginIn, request: Request, response: Response, db: DbSession = Depends(get_db)
) -> dict:
    if (wait := _locked(request)) > 0:
        raise HTTPException(429, f"Too many attempts. Try again in {wait} seconds.")

    stored = auth.get_password_hash(db)
    if not stored:
        raise HTTPException(400, "No password is set; the server is open.")

    # Both checked, one answer: saying which was wrong would let a guesser
    # confirm the password before ever facing the code.
    password_ok = auth.verify_password(body.password, stored)
    code_ok = password_ok and auth.verify_second_factor(db, body.code)
    if not (password_ok and code_ok):
        _record_failure(request)
        raise HTTPException(
            401, "Incorrect password or code." if auth.totp_enabled(db) else "Incorrect password."
        )

    _reset_failures(request)
    token = auth.issue_token(db)
    response.set_cookie(
        auth.COOKIE,
        token,
        max_age=auth.SESSION_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        # Secure whenever the browser is on HTTPS (Cloudflare), so the cookie
        # never travels in the clear; plain HTTP on the LAN still works.
        secure=_https(request),
        path="/",
    )
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response, db: DbSession = Depends(get_db)) -> dict:
    auth.revoke_token(db, request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.post("/password")
def set_password(
    body: PasswordIn, request: Request, db: DbSession = Depends(get_db)
) -> dict:
    """Set, change or clear the password.

    Reachable without a session only while no password exists -- that is how
    the gate gets closed in the first place. Once one is set, changing it needs
    both a valid session and the current password.
    """
    existing = auth.get_password_hash(db)
    if existing:
        if not auth.valid_token(db, request.cookies.get(auth.COOKIE)):
            raise HTTPException(401, "Sign in first.")
        if not body.current_password or not auth.verify_password(
            body.current_password, existing
        ):
            raise HTTPException(403, "Current password is incorrect.")

    new = body.password
    if new == "":
        auth.clear_password(db)
        return {"enabled": False}
    if len(new) < 8:
        raise HTTPException(400, "Use at least 8 characters.")

    auth.set_password(db, new)
    _reset_failures()
    # set_password revokes every session, including this one, so the caller has
    # to sign in again -- with the new password, on every device.
    return {"enabled": True}


# --- two-factor ----------------------------------------------------------------


class TotpSetupIn(BaseModel):
    current_password: str


class TotpCodeIn(BaseModel):
    code: str


class TotpDisableIn(BaseModel):
    current_password: str
    code: str


def _require_password(db: DbSession, given: str) -> None:
    stored = auth.get_password_hash(db)
    if not stored:
        raise HTTPException(400, "Set a password first; two-factor guards a password.")
    if not auth.verify_password(given, stored):
        raise HTTPException(403, "Current password is incorrect.")


@router.post("/totp/setup")
def totp_setup(body: TotpSetupIn, db: DbSession = Depends(get_db)) -> dict:
    """A new secret to add to an authenticator app. Not enforced until a code
    from the app confirms it (`/totp/enable`), so a half-done setup can't lock
    anyone out. Needs the password, so a stolen session can't swap the factor."""
    _require_password(db, body.current_password)
    if auth.totp_enabled(db):
        raise HTTPException(400, "Two-factor is already on. Turn it off first.")
    secret, uri = auth.begin_totp(db)
    return {"secret": secret, "uri": uri}


@router.post("/totp/enable")
def totp_enable(body: TotpCodeIn, request: Request, db: DbSession = Depends(get_db)) -> dict:
    codes = auth.enable_totp(db, body.code)
    if codes is None:
        raise HTTPException(400, "That code doesn't match. Check the phone's clock and try again.")
    # Any other device signed in before this has only ever proven the password.
    auth.revoke_all_except(db, request.cookies.get(auth.COOKIE))
    return {"recovery_codes": codes}


@router.post("/totp/disable")
def totp_disable(body: TotpDisableIn, db: DbSession = Depends(get_db)) -> dict:
    _require_password(db, body.current_password)
    if not auth.verify_second_factor(db, body.code):
        raise HTTPException(403, "That code doesn't match.")
    auth.disable_totp(db)
    return {"ok": True}
