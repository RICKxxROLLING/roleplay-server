"""Settings → Safety: who may be romantic or pictured, and why (memory/trust.py).

The rules for anyone written as under 18 are fixed and not settings: always platonic, never
pictured, no romantic or physical choices offered. What the owner chooses is how adults are
treated: romance for tagged cards, any adult or nobody, and what a card with no age counts as.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from ..cards.models import CharacterCard
from ..db import Character, get_db
from ..memory import trust

router = APIRouter(prefix="/safety", tags=["safety"])


def person(c: Character) -> dict:
    card = CharacterCard.model_validate(c.card or {"name": c.name})
    minor = trust.is_minor(card)
    return {
        "id": c.id, "name": c.name, "extra": bool(c.extra),
        "age": trust.stated_age(card), "minor": minor, "age_unknown": trust.age_unknown(card),
        "romance_tag": trust.has_romance_tag(card),
        "romance": trust.romance_allowed(card),
        "pictured": not trust.kept_platonic(card),
        # Whether explicit content could involve them, if the switches in Safety are on.
        "explicit_eligible": trust.adult_for_explicit(card),
        "explicit_note": trust.explicit_note(card),
    }


@router.get("/people")
def people(db: DbSession = Depends(get_db)) -> list[dict]:
    rows = db.query(Character).filter(Character.host.is_(False)).order_by(Character.name).all()
    return [person(c) for c in rows]


class AgeIn(BaseModel):
    age: int = Field(..., ge=18, le=120)


@router.put("/people/{char_id}/age")
def set_age(char_id: int, body: AgeIn, db: DbSession = Depends(get_db)) -> dict:
    """Add an adult age to a card that doesn't state one, as a sentence at the end of its
    description, so the age rules can read it. Refused for anyone written as under 18."""
    c = db.get(Character, char_id)
    if not c or c.host:
        raise HTTPException(404, "Character not found")
    card = CharacterCard.model_validate(c.card or {"name": c.name})
    if trust.is_minor(card):
        raise HTTPException(400, f"{c.name} is written as under 18; that can't be changed here.")
    if trust.stated_age(card) is not None:
        raise HTTPException(400, f"{c.name}'s card already states an age. Edit the card to change it.")
    first = c.name.split()[0]
    description = (card.description.rstrip() + "\n\n" + f"{first} is {body.age} years old.").strip()
    c.card = {**card.model_dump(), "description": description}
    db.commit()
    return person(c)


class RomanceIn(BaseModel):
    tagged: bool


@router.put("/people/{char_id}/romance")
def set_romance(char_id: int, body: RomanceIn, db: DbSession = Depends(get_db)) -> dict:
    """Add or remove the card's `romance` tag. Refused for anyone written as under 18."""
    c = db.get(Character, char_id)
    if not c or c.host:
        raise HTTPException(404, "Character not found")
    card = CharacterCard.model_validate(c.card or {"name": c.name})
    if body.tagged and trust.is_minor(card):
        raise HTTPException(400, f"{c.name} is written as under 18, so romance stays off.")
    tags = [t for t in card.tags if t.strip().lower() != "romance"] + (["romance"] if body.tagged else [])
    c.card = {**card.model_dump(), "tags": tags}
    db.commit()
    return person(c)
