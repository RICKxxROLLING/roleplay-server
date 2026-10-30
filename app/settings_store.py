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
from . import profiles
from .db.models import AppSetting

PERSISTABLE: dict[str, type] = {
    "backend": str,
    "llm_base_url": str,
    "model": str,
    "prompt_format": str,
    "narration_style": str,
    "guard_user_actions": bool,
    "redo_first_person": bool,
    "context_tokens": int,
    "max_new_tokens": int,
    "reserve_tokens": int,
    "temperature": float,
    "top_p": float,
    "top_k": int,
    "min_p": float,
    "repeat_penalty": float,
    "repeat_last_n": int,
    "summary_enabled": bool,
    "summary_trigger_tokens": int,
    "keep_recent_messages": int,
    "summary_max_tokens": int,
    "summary_temperature": float,
    "facts_enabled": bool,
    "scene_enabled": bool,
    "thoughts_enabled": bool,
    "timezone": str,
    "autonomy_quiet_start": str,
    "autonomy_quiet_end": str,
    "autonomy_min_gap_minutes": int,
    "autonomy_max_unanswered": int,
    "autonomy_max_per_day": int,
    "autonomy_check_minutes": int,
    "retrieval_enabled": bool,
    "embedding_model": str,
    "embedding_base_url": str,
    "retrieval_top_k": int,
    "retrieval_min_score": float,
    "retrieval_budget_tokens": int,
    "retrieval_query_messages": int,
}

# Changing any of these invalidates the cached LLM client.
CLIENT_KEYS = {"backend", "llm_base_url", "model"}

# ...and these the cached embedder. `backend` and `llm_base_url` appear in both:
# the embedder inherits the backend flavour, and the chat host when
# embedding_base_url is blank.
EMBEDDER_KEYS = {"backend", "llm_base_url", "embedding_model", "embedding_base_url"}


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


def _put(db: DbSession, key: str, value: Any) -> bool:
    coerced = PERSISTABLE[key](value)
    if getattr(settings, key) == coerced:
        return False
    setattr(settings, key, coerced)
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=coerced))
    else:
        row.value = coerced
    return True


def save(
    db: DbSession, patch: dict[str, Any], report: dict[str, Any] | None = None
) -> set[str]:
    """Persist and apply a partial settings update. Returns the keys changed.

    A model switch also swaps in the new model's profile (profiles.py): its
    saved settings, else a built-in preset. Keys sent in the same patch win
    over the profile. `report`, when given, receives what was applied.
    """
    changed: set[str] = set()
    switching = patch.get("model") not in (None, settings.model)
    if switching:
        # Before anything changes: a patch carrying a new model *and* a new
        # temperature must not save that temperature into the old profile.
        profiles.remember(db, settings.model)

    for key, value in patch.items():
        if key not in PERSISTABLE or value is None:
            continue
        if _put(db, key, value):
            changed.add(key)

    if "model" in changed:
        values, source, label = profiles.recall(db, settings.model)
        for key, value in values.items():
            if key not in patch and _put(db, key, value):
                changed.add(key)
        if report is not None:
            report.update(model=settings.model, source=source, label=label,
                          applied=sorted(values))

    if changed & set(profiles.PROFILE_KEYS) or "model" in changed:
        profiles.remember(db, settings.model)

    if changed:
        db.commit()
    return changed


def current() -> dict[str, Any]:
    return {k: getattr(settings, k) for k in PERSISTABLE}
