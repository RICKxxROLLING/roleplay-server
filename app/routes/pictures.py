"""Scene pictures and portraits from ComfyUI on the owner's network (app/images.py)."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DbSession

from .. import images
from ..chat import autonomy, group
from ..db import Character, ChatSession, get_db
from ..db.models import Picture

router = APIRouter(tags=["pictures"])


@router.get("/pictures/status")
async def picture_status() -> dict:
    return await images.status()


def picture_out(p: Picture) -> dict:
    return {"id": p.id, "after_message_id": p.after_message_id, "prompt": p.prompt,
            "url": f"/api/pictures/{p.id}"}


@router.post("/sessions/{session_id}/pictures")
async def picture_of_this_moment(session_id: int, db: DbSession = Depends(get_db)) -> dict:
    """A picture of where the chat is now, placed after its latest message."""
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    cast = group.cast(s)
    young = images.minors(cast)
    if young:
        raise HTTPException(400, f"No pictures of {', '.join(young)}: written as under 18.")
    if not s.messages or not cast:
        raise HTTPException(400, "There's nothing to picture yet")
    user = s.persona.name if s.persona else "You"
    recent = [(user if m.role == "user" else group.speaker_name(s, m), m.content)
              for m in s.messages if m.role in ("user", "assistant", "narrator")][-6:]
    autonomy.ACTIVE += 1  # The writing-first scheduler waits while the chat model is busy.
    try:
        prompt = await images.scene_prompt(s, cast, user, recent)
    except images.ImagesUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        autonomy.ACTIVE -= 1
    db.commit()  # Any look written for this picture is kept, whether or not ComfyUI answers.
    try:
        data = await images.render(prompt, images.SCENE_SIZE)
    except images.ImagesUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    p = Picture(session_id=s.id, after_message_id=s.messages[-1].id, path=images.save(data), prompt=prompt)
    db.add(p)
    db.commit()
    return picture_out(p)


@router.get("/pictures/{picture_id}")
def get_picture(picture_id: int, db: DbSession = Depends(get_db)) -> FileResponse:
    p = db.get(Picture, picture_id)
    if not p or not os.path.exists(p.path):
        raise HTTPException(404, "Picture not found")
    return FileResponse(p.path, media_type="image/png")


@router.delete("/pictures/{picture_id}")
def delete_picture(picture_id: int, db: DbSession = Depends(get_db)) -> dict:
    p = db.get(Picture, picture_id)
    if not p:
        raise HTTPException(404, "Picture not found")
    if os.path.exists(p.path):
        os.remove(p.path)
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.post("/characters/{char_id}/portrait")
async def make_portrait(char_id: int, db: DbSession = Depends(get_db)) -> dict:
    """A portrait from the card, kept as their avatar. Replaces the old one."""
    c = db.get(Character, char_id)
    if not c or c.host:
        raise HTTPException(404, "Character not found")
    if images.minors([c]):
        raise HTTPException(400, f"No pictures of {c.name}: written as under 18.")
    autonomy.ACTIVE += 1
    try:
        prompt = await images.portrait_prompt(c)
    except images.ImagesUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        autonomy.ACTIVE -= 1
    db.commit()
    try:
        data = await images.render(prompt, images.PORTRAIT_SIZE)
    except images.ImagesUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    old = c.avatar_path
    c.avatar_path = images.save(data)
    db.commit()
    if old and os.path.exists(old) and old != c.avatar_path:
        os.remove(old)
    return {"id": c.id, "has_avatar": True, "prompt": prompt}
