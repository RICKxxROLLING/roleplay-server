from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..cards import people
from ..cards.models import LoreEntry
from ..db import Character, World, get_db
from ..db.models import Persona

router = APIRouter(prefix="/worlds", tags=["worlds"])


class WorldIn(BaseModel):
    name: str
    description: str = ""
    # Validated through the card lorebook's schema, so world entries behave
    # exactly like card entries.
    entries: list[LoreEntry] = []


class WorldPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    entries: list[LoreEntry] | None = None


def _out(w: World) -> dict:
    return {
        "id": w.id,
        "name": w.name,
        "description": w.description,
        "entries": w.entries or [],
        "characters": [{"id": c.id, "name": c.name, "extra": bool(c.extra)} for c in w.characters],
    }


@router.get("")
def list_worlds(db: DbSession = Depends(get_db)) -> list[dict]:
    return [_out(w) for w in db.query(World).order_by(World.name).all()]


@router.post("")
def create_world(body: WorldIn, db: DbSession = Depends(get_db)) -> dict:
    if not body.name.strip():
        raise HTTPException(400, "A world needs a name")
    w = World(name=body.name.strip(), description=body.description,
              entries=[e.model_dump() for e in body.entries])
    db.add(w)
    db.commit()
    db.refresh(w)
    return _out(w)


@router.get("/{world_id}")
def get_world(world_id: int, db: DbSession = Depends(get_db)) -> dict:
    w = db.get(World, world_id)
    if not w:
        raise HTTPException(404, "World not found")
    return _out(w)


@router.patch("/{world_id}")
def update_world(world_id: int, body: WorldPatch, db: DbSession = Depends(get_db)) -> dict:
    w = db.get(World, world_id)
    if not w:
        raise HTTPException(404, "World not found")
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(400, "A world needs a name")
        w.name = body.name.strip()
    if body.description is not None:
        w.description = body.description
    if body.entries is not None:
        w.entries = [e.model_dump() for e in body.entries]
    db.commit()
    db.refresh(w)
    return _out(w)


@router.delete("/{world_id}")
def delete_world(world_id: int, db: DbSession = Depends(get_db)) -> dict:
    """Characters in it are kept and simply leave the world."""
    w = db.get(World, world_id)
    if not w:
        raise HTTPException(404, "World not found")
    for c in list(w.characters):
        c.world_id = None
    db.delete(w)
    db.commit()
    return {"ok": True}


class PersonIn(BaseModel):
    #: Optional steer in the owner's words: "a midwife who hates horses".
    hint: str = ""


@router.post("/{world_id}/people/draft")
def draft_person(world_id: int, body: PersonIn, db: DbSession = Depends(get_db)) -> StreamingResponse:
    """Stream a new person for this world (cards/people.py). Nothing is saved: the owner
    keeps them through the ordinary character endpoint, or discards them."""
    w = db.get(World, world_id)
    if not w:
        raise HTTPException(404, "World not found")
    personas = [p.name for p in db.query(Persona).all()]
    taken = {c.name for c in db.query(Character).all()} | set(personas)

    async def gen():
        try:
            async for event in people.stream_person(w, taken=taken, user_names=personas, hint=body.hint):
                yield f"data: {json.dumps(event)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': f'{type(exc).__name__}: {exc}'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class Membership(BaseModel):
    world_id: int | None = None


@router.put("/membership/{char_id}")
def set_membership(char_id: int, body: Membership, db: DbSession = Depends(get_db)) -> dict:
    """Put a character in a world, or take it out with `world_id: null`."""
    c = db.get(Character, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    if body.world_id is not None and not db.get(World, body.world_id):
        raise HTTPException(404, "World not found")
    c.world_id = body.world_id
    db.commit()
    return {"id": c.id, "world_id": c.world_id}
