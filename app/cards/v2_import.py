"""SillyTavern / TavernAI character card importer.

Cards ship as PNGs with the card JSON base64-encoded into a tEXt chunk keyed
`chara` (V1) or `ccv3` (V3). V2 keeps the `chara` key but wraps the payload as
{"spec": "chara_card_v2", "data": {...}}. We handle V1, V2 and V3-in-V2-shape.
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from PIL import Image

from .models import CharacterCard, LoreEntry

_TEXT_KEYS = ("ccv3", "chara")


class CardImportError(ValueError):
    pass


def _decode_chunk(raw: str) -> dict[str, Any]:
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise CardImportError(f"Card metadata is not valid base64/UTF-8: {exc}") from exc
    try:
        return json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise CardImportError(f"Card metadata is not valid JSON: {exc}") from exc


def extract_card_json(png_path: str) -> dict[str, Any]:
    """Pull the embedded card JSON out of a character PNG."""
    with Image.open(png_path) as img:
        meta = getattr(img, "text", {}) or {}
        # Pillow exposes tEXt in .text; some cards use info instead.
        if not meta:
            meta = {k: v for k, v in (img.info or {}).items() if isinstance(v, str)}
        for key in _TEXT_KEYS:
            if key in meta:
                return _decode_chunk(meta[key])
    raise CardImportError(
        "No character metadata found in PNG (expected a 'chara' or 'ccv3' tEXt chunk)."
    )


def _as_list(value: Any) -> list[str]:
    """Keys are usually a list, but some cards ship a comma-separated string."""
    if isinstance(value, str):
        return [k.strip() for k in value.split(",") if k.strip()]
    if isinstance(value, list):
        return [str(k).strip() for k in value if str(k).strip()]
    return []


def _parse_book(book: Any) -> list[LoreEntry]:
    if not isinstance(book, dict):
        return []
    entries = []
    for raw in book.get("entries") or []:
        if not isinstance(raw, dict):
            continue
        position = raw.get("position")
        if position not in {"before_char", "after_char"}:
            position = "after_char"
        entries.append(
            LoreEntry(
                keys=_as_list(raw.get("keys")),
                content=raw.get("content") or "",
                enabled=raw.get("enabled", True),
                insertion_order=raw.get("insertion_order", 100),
                case_sensitive=bool(raw.get("case_sensitive", False)),
                priority=raw.get("priority", 10),
                selective=bool(raw.get("selective", False)),
                secondary_keys=_as_list(raw.get("secondary_keys")),
                constant=bool(raw.get("constant", False)),
                position=position,
                name=raw.get("name") or "",
                comment=raw.get("comment") or "",
            )
        )
    return entries


def _book_settings(book: Any) -> dict[str, Any]:
    if not isinstance(book, dict):
        return {}
    out: dict[str, Any] = {}
    if isinstance(book.get("scan_depth"), int):
        out["lorebook_scan_depth"] = max(1, book["scan_depth"])
    if isinstance(book.get("token_budget"), int):
        out["lorebook_token_budget"] = max(0, book["token_budget"])
    if "recursive_scanning" in book:
        out["lorebook_recursive"] = bool(book["recursive_scanning"])
    return out


def normalise(payload: dict[str, Any]) -> CharacterCard:
    """Flatten V1/V2/V3 payloads into our internal schema."""
    # V2/V3 nest the real fields under "data"; V1 is flat.
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    return CharacterCard(
        name=data.get("name") or "Unnamed",
        description=data.get("description") or "",
        personality=data.get("personality") or "",
        scenario=data.get("scenario") or "",
        first_mes=data.get("first_mes") or "",
        mes_example=data.get("mes_example") or "",
        creator_notes=data.get("creator_notes") or "",
        system_prompt=data.get("system_prompt") or "",
        post_history_instructions=data.get("post_history_instructions") or "",
        alternate_greetings=data.get("alternate_greetings") or [],
        tags=data.get("tags") or [],
        creator=data.get("creator") or "",
        character_version=str(data.get("character_version") or ""),
        character_book=_parse_book(data.get("character_book")),
        **_book_settings(data.get("character_book")),
    )


def load_card(png_path: str) -> CharacterCard:
    return normalise(extract_card_json(png_path))
