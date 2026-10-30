"""Import a bundle: worlds and characters in one JSON file, from the UI.

The way to load a whole setting at once -- a world, its lorebook, and the cast
that lives in it -- onto an install you can only reach through the browser.
Matching is by name, case-insensitive:

- a world that exists has its description and entries replaced;
- a character that exists is *updated* only when `replace` is set -- its card
  text and world change, its chats, avatar and journal stay. Otherwise it is
  skipped, so importing twice never makes duplicates.

Format:
    {"format": "roleplay-bundle", "version": 1,
     "worlds": [{"name", "description", "entries": [LoreEntry, ...]}],
     "characters": [{"world": "<world name or null>", "card": <V1/V2 card JSON>}]}
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..cards.models import LoreEntry
from ..cards.v2_import import normalise
from ..db import Character, World, get_db

router = APIRouter(prefix="/import", tags=["import"])

FORMAT = "roleplay-bundle"


class WorldIn(BaseModel):
    name: str
    description: str = ""
    entries: list[LoreEntry] = []


class CharacterIn(BaseModel):
    world: str | None = None
    card: dict[str, Any]


class BundleIn(BaseModel):
    format: str
    version: int = 1
    worlds: list[WorldIn] = []
    characters: list[CharacterIn] = []
    #: Update characters that already exist by name instead of skipping them.
    replace: bool = False


@router.post("/bundle")
def import_bundle(body: BundleIn, db: DbSession = Depends(get_db)) -> dict:
    if body.format != FORMAT:
        raise HTTPException(400, f"Not a {FORMAT} file.")
    report: dict[str, list[str]] = {"worlds_created": [], "worlds_updated": [],
                                    "characters_created": [], "characters_updated": [],
                                    "characters_skipped": []}

    worlds = {w.name.lower(): w for w in db.query(World).all()}
    for w in body.worlds:
        if not w.name.strip():
            raise HTTPException(400, "A world needs a name.")
        entries = [e.model_dump() for e in w.entries]
        existing = worlds.get(w.name.strip().lower())
        if existing:
            existing.description, existing.entries = w.description, entries
            report["worlds_updated"].append(existing.name)
        else:
            existing = World(name=w.name.strip(), description=w.description, entries=entries)
            db.add(existing)
            report["worlds_created"].append(existing.name)
        worlds[existing.name.lower()] = existing
    db.flush()

    chars = {c.name.lower(): c for c in db.query(Character).all()}
    for item in body.characters:
        card = normalise(item.card)
        if not card.name.strip() or card.name == "Unnamed":
            raise HTTPException(400, "Every character needs a name.")
        world = worlds.get((item.world or "").lower()) if item.world else None
        if item.world and world is None:
            raise HTTPException(400, f"{card.name}: no world named {item.world!r} in this bundle or server.")
        existing = chars.get(card.name.lower())
        if existing and not body.replace:
            report["characters_skipped"].append(existing.name)
            continue
        if existing:
            # The card text is replaced wholesale; chats, avatar and journal stay.
            existing.card = card.model_dump()
            existing.world_id = world.id if world else None
            report["characters_updated"].append(existing.name)
        else:
            c = Character(name=card.name, card=card.model_dump(), avatar_path=None,
                          world_id=world.id if world else None)
            db.add(c)
            chars[card.name.lower()] = c
            report["characters_created"].append(card.name)
    db.commit()
    return report
