from __future__ import annotations

import json
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..cards import CardImportError, creator, load_card
from ..cards.models import CharacterCard, LoreEntry
from ..config import settings
from ..db import Character, SessionMember, World, get_db

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


class DraftIn(BaseModel):
    #: The idea, in the user's words. May be empty when expanding a card.
    brief: str = ""
    #: Set the new character in this world: its setting, lore names and cast.
    world_id: int | None = None
    #: Flesh out this card instead of starting from nothing.
    character_id: int | None = None


@router.post("/draft")
def draft_character(body: DraftIn, db: DbSession = Depends(get_db)) -> StreamingResponse:
    """Stream a drafted card (cards/creator.py). Nothing is saved: the editor
    shows the draft and the user keeps what they want."""
    world = db.get(World, body.world_id) if body.world_id else None
    if body.world_id and not world:
        raise HTTPException(404, "World not found")
    existing = db.get(Character, body.character_id) if body.character_id else None
    if body.character_id and not existing:
        raise HTTPException(404, "Character not found")
    if not body.brief.strip() and existing is None:
        raise HTTPException(400, "Describe the character first")
    card = CharacterCard.model_validate(existing.card or {}) if existing else None
    if world is None and existing is not None:
        world = existing.world
    taken = {c.name.lower() for c in db.query(Character).all()
             if existing is None or c.id != existing.id}

    async def gen():
        try:
            async for event in creator.stream_draft(body.brief, world, card):
                if event["type"] == "draft":
                    name = event["fields"].get("name", "")
                    # A second "Dina" would make every group chat and lore
                    # mention ambiguous; say so rather than refuse.
                    event["name_taken"] = name.lower() in taken
                yield f"data: {json.dumps(event)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': f'{type(exc).__name__}: {exc}'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
            "world_id": c.world_id,
        }
        for c in rows
    ]


@router.get("/{char_id}")
def get_character(char_id: int, db: DbSession = Depends(get_db)) -> dict:
    c = db.get(Character, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    return {"id": c.id, "name": c.name, "card": c.card, "world_id": c.world_id}


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
    # Leaves any group chat it was in; its old lines there keep their text and
    # read as "Someone" (chat/group.py).
    db.query(SessionMember).filter(SessionMember.character_id == c.id).delete()
    from ..db.models import CharacterThought

    db.query(CharacterThought).filter(CharacterThought.character_id == c.id).delete()
    db.delete(c)
    db.commit()
    return {"ok": True}
