from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..cards import CardImportError, load_card
from ..cards.models import CharacterCard, LoreEntry
from ..config import settings
from ..db import Character, get_db

router = APIRouter(prefix="/characters", tags=["characters"])

AVATAR_DIR = os.path.join(settings.data_dir, "avatars")


def _avatar_dir() -> str:
    """Created on demand rather than at import -- see the note in db/database.py."""
    os.makedirs(AVATAR_DIR, exist_ok=True)
    return AVATAR_DIR


@router.post("/import")
async def import_card(
    file: UploadFile = File(...), db: DbSession = Depends(get_db)
) -> dict:
    """Upload a SillyTavern V2 character PNG."""
    if not (file.filename or "").lower().endswith(".png"):
        raise HTTPException(400, "Character cards must be PNG files.")

    path = os.path.join(_avatar_dir(), f"{uuid.uuid4().hex}.png")
    with open(path, "wb") as fh:
        fh.write(await file.read())

    try:
        card = load_card(path)
    except CardImportError as exc:
        os.remove(path)
        raise HTTPException(422, str(exc)) from exc

    char = Character(name=card.name, card=card.model_dump(), avatar_path=path)
    db.add(char)
    db.commit()
    db.refresh(char)
    return {"id": char.id, "name": char.name, "card": char.card}


class CharacterIn(BaseModel):
    """A character written by hand rather than imported from a PNG.

    Only `name` is required. Everything else is optional so a character can be
    started from an idea and filled in from the editor afterwards -- the same
    fields, the same validation, just no card file behind it.
    """

    name: str
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    system_prompt: str = ""
    post_history_instructions: str = ""
    alternate_greetings: list[str] = []
    tags: list[str] = []

    character_book: list[LoreEntry] = []


@router.post("")
def create_character(body: CharacterIn, db: DbSession = Depends(get_db)) -> dict:
    """Create a character from scratch.

    Stored in exactly the same shape as an imported card, so everything
    downstream -- prompt building, the editor, the lorebook -- cannot tell the
    difference. The only thing a hand-written character lacks is `avatar_path`,
    which is already nullable for cards whose PNG went missing.
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Character name is required")

    # Round-trip through the schema so a hand-written character can't be created
    # in a shape the importer would have rejected.
    card = CharacterCard.model_validate({**body.model_dump(), "name": name})

    char = Character(name=card.name, card=card.model_dump(), avatar_path=None)
    db.add(char)
    db.commit()
    db.refresh(char)
    return {"id": char.id, "name": char.name, "card": char.card}


@router.get("")
def list_characters(db: DbSession = Depends(get_db)) -> list[dict]:
    rows = db.query(Character).order_by(Character.name).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "has_avatar": bool(c.avatar_path),
            "tags": (c.card or {}).get("tags", []),
            "description": ((c.card or {}).get("description") or "")[:280],
        }
        for c in rows
    ]


@router.get("/{char_id}")
def get_character(char_id: int, db: DbSession = Depends(get_db)) -> dict:
    c = db.get(Character, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    return {"id": c.id, "name": c.name, "card": c.card}


class CardPatch(BaseModel):
    """Editable card fields. Anything omitted is left alone."""

    name: str | None = None
    description: str | None = None
    personality: str | None = None
    scenario: str | None = None
    first_mes: str | None = None
    mes_example: str | None = None
    system_prompt: str | None = None
    post_history_instructions: str | None = None
    alternate_greetings: list[str] | None = None
    tags: list[str] | None = None

    character_book: list[LoreEntry] | None = None
    lorebook_scan_depth: int | None = None
    lorebook_token_budget: int | None = None
    lorebook_recursive: bool | None = None


@router.patch("/{char_id}")
def update_character(
    char_id: int, body: CardPatch, db: DbSession = Depends(get_db)
) -> dict:
    c = db.get(Character, char_id)
    if not c:
        raise HTTPException(404, "Character not found")

    patch = body.model_dump(exclude_none=True)
    if not patch:
        return {"id": c.id, "name": c.name, "card": c.card}

    # Round-trip through the schema so an edit can't write a malformed card.
    merged = {**(c.card or {}), **patch}
    card = CharacterCard.model_validate(merged)

    c.card = card.model_dump()
    c.name = card.name
    # SQLAlchemy won't notice in-place JSON mutation; reassignment above is
    # what marks the column dirty.
    db.commit()
    db.refresh(c)
    return {"id": c.id, "name": c.name, "card": c.card}


@router.get("/{char_id}/avatar")
def get_avatar(char_id: int, db: DbSession = Depends(get_db)) -> FileResponse:
    c = db.get(Character, char_id)
    if not c or not c.avatar_path or not os.path.exists(c.avatar_path):
        raise HTTPException(404, "No avatar for this character")
    return FileResponse(c.avatar_path, media_type="image/png")


@router.delete("/{char_id}")
def delete_character(char_id: int, db: DbSession = Depends(get_db)) -> dict:
    c = db.get(Character, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    if c.avatar_path and os.path.exists(c.avatar_path):
        os.remove(c.avatar_path)
    db.delete(c)
    db.commit()
    return {"ok": True}
