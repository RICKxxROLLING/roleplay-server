"""Login, logout, and setting the password.

These are the only endpoints under /api that answer without a session, so the
surface here is the whole surface. It is kept to four routes for that reason.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from .. import auth
from ..db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

#: Failed attempts before the gate stops answering, and how long it stays shut.
#: A shared password is a small keyspace, so an unthrottled endpoint is a
#: guessing oracle. Held in memory on purpose -- a restart clearing the counter
#: is acceptable, and it keeps failed attempts out of the database.
_MAX_FAILURES = 8
_LOCKOUT_SECONDS = 300
_failures = 0
_locked_until = 0.0


def _record_failure() -> None:
    global _failures, _locked_until
    _failures += 1
    if _failures >= _MAX_FAILURES:
        _locked_until = time.monotonic() + _LOCKOUT_SECONDS
        _failures = 0


def _reset_failures() -> None:
    global _failures, _locked_until
    _failures = 0
    _locked_until = 0.0


def _locked() -> int:
    remaining = _locked_until - time.monotonic()
    return int(remaining) if remaining > 0 else 0


class LoginIn(BaseModel):
    password: str


class PasswordIn(BaseModel):
    password: str
    #: Required once a password exists, so a stolen session cannot silently
    #: change the lock and evict the owner.
    current_password: str | None = None


@router.get("/status")
def status(request: Request, db: DbSession = Depends(get_db)) -> dict:
    """Public by design: the UI has to know whether to show a login screen."""
    enabled = auth.is_enabled(db)
    return {
        "enabled": enabled,
        "authenticated": (
            not enabled or auth.valid_token(db, request.cookies.get(auth.COOKIE))
        ),
        "locked_for": _locked(),
    }


@router.post("/login")
def login(
    body: LoginIn, response: Response, db: DbSession = Depends(get_db)
) -> dict:
    if (wait := _locked()) > 0:
        raise HTTPException(429, f"Too many attempts. Try again in {wait} seconds.")

    stored = auth.get_password_hash(db)
    if not stored:
        raise HTTPException(400, "No password is set; the server is open.")

    if not auth.verify_password(body.password, stored):
        _record_failure()
        raise HTTPException(401, "Incorrect password.")

    _reset_failures()
    token = auth.issue_token(db)
    response.set_cookie(
        auth.COOKIE,
        token,
        max_age=auth.SESSION_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        # Not `secure`: this is normally reached over plain HTTP on a LAN or a
        # VPN address. Behind a TLS-terminating proxy, set RP_COOKIE_SECURE.
        secure=False,
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
