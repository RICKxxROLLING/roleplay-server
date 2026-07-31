from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..db import ChatSession, Persona, get_db

router = APIRouter(prefix="/personas", tags=["personas"])


class PersonaIn(BaseModel):
    name: str
    description: str = ""


@router.post("")
def create_persona(body: PersonaIn, db: DbSession = Depends(get_db)) -> dict:
    p = Persona(name=body.name, description=body.description)
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"id": p.id, "name": p.name, "description": p.description}


@router.get("")
def list_personas(db: DbSession = Depends(get_db)) -> list[dict]:
    return [
        {"id": p.id, "name": p.name, "description": p.description}
        for p in db.query(Persona).order_by(Persona.name).all()
    ]


class PersonaPatch(BaseModel):
    name: str | None = None
    description: str | None = None


@router.patch("/{persona_id}")
def update_persona(
    persona_id: int, body: PersonaPatch, db: DbSession = Depends(get_db)
) -> dict:
    p = db.get(Persona, persona_id)
    if not p:
        raise HTTPException(404, "Persona not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return {"id": p.id, "name": p.name, "description": p.description}


@router.delete("/{persona_id}")
def delete_persona(persona_id: int, db: DbSession = Depends(get_db)) -> dict:
    p = db.get(Persona, persona_id)
    if not p:
        raise HTTPException(404, "Persona not found")

    # Detach it from any chats first -- otherwise those sessions keep a dangling
    # persona_id and blow up on load. They fall back to "You".
    orphaned = (
        db.query(ChatSession).filter(ChatSession.persona_id == persona_id).all()
    )
    for s in orphaned:
        s.persona_id = None

    db.delete(p)
    db.commit()
    return {"ok": True, "chats_detached": len(orphaned)}
