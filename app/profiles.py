"""Per-model settings: the format and samplers travel with the model.

Before this, switching models kept whatever format and samplers the last
model used. That is exactly how L3-8B-Stheno ended up answering Alpaca
prompts -- it still replied, so nothing looked broken, while 18 of 48 replies
ran into the length cap and the narrator drifted into first person.

Two sources, in order:
  1. the model's saved profile -- the settings it was last used with, saved
     whenever one of PROFILE_KEYS changes and when switching away from it;
  2. a built-in preset matched on the model's name, for a model's first use.
A model with neither keeps the current settings, and the UI says so.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session as DbSession

from .config import settings
from .db.models import ModelProfile

#: What a profile carries: everything that depends on the model rather than
#: on taste in memory or retrieval.
PROFILE_KEYS = (
    "prompt_format",
    "context_tokens",
    "max_new_tokens",
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "repeat_penalty",
    "repeat_last_n",
)


@dataclass(frozen=True)
class Preset:
    #: Lowercase substrings of the model name; any one matches.
    match: tuple[str, ...]
    label: str
    values: dict[str, Any] = field(default_factory=dict)


# First match wins, so specific tunes sit above the family they belong to.
# A preset sets only what is known about the model; anything it leaves out
# keeps its current value.
PRESETS: tuple[Preset, ...] = (
    Preset(
        ("stheno",),
        "Stheno v3.2 (Llama 3) — measured on this project",
        {
            "prompt_format": "llama3", "context_tokens": 8192, "max_new_tokens": 512,
            "temperature": 1.12, "top_p": 1.0, "top_k": 50, "min_p": 0.075,
            "repeat_penalty": 1.1, "repeat_last_n": 1024,
        },
    ),
    Preset(
        ("llama-3", "llama3", "l3-", "l3.", "lumimaid"),
        "Llama 3 family",
        {"prompt_format": "llama3", "context_tokens": 8192},
    ),
    Preset(
        ("qwen", "chatml", "nemo"),
        "ChatML family (Qwen, Mistral-Nemo tunes)",
        {"prompt_format": "chatml"},
    ),
    Preset(
        ("smart-lemon-cookie", "mythomax", "kunoichi", "silicon-maid", "lemonade"),
        "Alpaca merges (MythoMax, Smart-Lemon-Cookie)",
        {
            "prompt_format": "alpaca", "temperature": 0.9, "top_p": 0.9, "top_k": 40,
            "min_p": 0.0, "repeat_penalty": 1.1, "repeat_last_n": 1024,
        },
    ),
)


def preset_for(model: str) -> Preset | None:
    name = (model or "").lower()
    return next((p for p in PRESETS if any(m in name for m in p.match)), None)


def snapshot() -> dict[str, Any]:
    return {k: getattr(settings, k) for k in PROFILE_KEYS}


def remember(db: DbSession, model: str) -> None:
    """Save the current profile settings as `model`'s. Caller commits."""
    if not model:
        return
    row = db.get(ModelProfile, model)
    if row is None:
        db.add(ModelProfile(model=model, values=snapshot()))
    else:
        row.values = snapshot()
        row.updated_at = dt.datetime.now(dt.timezone.utc)


def recall(db: DbSession, model: str) -> tuple[dict[str, Any], str, str]:
    """(values, source, label) for `model`. source is saved | preset | none."""
    row = db.get(ModelProfile, model)
    if row is not None:
        return {k: v for k, v in (row.values or {}).items() if k in PROFILE_KEYS}, "saved", ""
    preset = preset_for(model)
    if preset is not None:
        return dict(preset.values), "preset", preset.label
    return {}, "none", ""
