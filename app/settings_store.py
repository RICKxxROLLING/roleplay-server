"""Persistence for runtime-editable settings.

The UI is the source of truth, so anything changed there has to survive a
restart. Environment variables seed the defaults on first boot; once a key is
written here it takes precedence.

Only fields that are safe to change at runtime are persistable -- paths, the
database URL and CORS are deliberately excluded, since changing those without a
restart would leave the process in an inconsistent state.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session as DbSession

from .config import settings
from .db.models import AppSetting

PERSISTABLE: dict[str, type] = {
    "backend": str,
    "llm_base_url": str,
    "model": str,
    "context_tokens": int,
    "max_new_tokens": int,
    "reserve_tokens": int,
    "temperature": float,
    "top_p": float,
    "top_k": int,
    "repeat_penalty": float,
    "summary_enabled": bool,
    "summary_trigger_tokens": int,
    "keep_recent_messages": int,
    "summary_max_tokens": int,
    "summary_temperature": float,
}

# Changing any of these invalidates the cached LLM client.
CLIENT_KEYS = {"backend", "llm_base_url", "model"}


def load_into_settings(db: DbSession) -> int:
    """Apply persisted overrides onto the in-process settings object."""
    applied = 0
    for row in db.query(AppSetting).all():
        if row.key not in PERSISTABLE:
            continue
        try:
            setattr(settings, row.key, PERSISTABLE[row.key](row.value))
            applied += 1
        except (TypeError, ValueError):
            # A malformed row shouldn't stop the app booting.
            continue
    return applied


def save(db: DbSession, patch: dict[str, Any]) -> set[str]:
    """Persist and apply a partial settings update. Returns the keys changed."""
    changed: set[str] = set()

    for key, value in patch.items():
        if key not in PERSISTABLE or value is None:
            continue
        coerced = PERSISTABLE[key](value)
        if getattr(settings, key) == coerced:
            continue

        setattr(settings, key, coerced)
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=coerced))
        else:
            row.value = coerced
        changed.add(key)

    if changed:
        db.commit()
    return changed


def current() -> dict[str, Any]:
    return {k: getattr(settings, k) for k in PERSISTABLE}
