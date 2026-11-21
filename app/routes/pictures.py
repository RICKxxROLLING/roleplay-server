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


def _explicit(people, s: ChatSession | None = None) -> dict:
    """Whether a picture of these people may be explicit, and if not, why (for the refusal)."""
    why = images.explicit_block(people, s.persona.description if s is not None and s.persona else "")
    return {"allow_explicit": not why, "why_not": why}


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
    refused = images.not_pictured(cast)
    if refused:
        raise HTTPException(400, refused)
    if not s.messages or not cast:
        raise HTTPException(400, "There's nothing to picture yet")
    user = s.persona.name if s.persona else "You"
    recent = [(user if m.role == "user" else group.speaker_name(s, m), m.content)
              for m in s.messages if m.role in ("user", "assistant", "narrator")][-6:]
    autonomy.ACTIVE += 1  # The writing-first scheduler waits while the chat model is busy.
    try:
        scene = await images.scene_prompt(s, cast, user, recent)
    except images.ImagesUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        autonomy.ACTIVE -= 1
    db.commit()  # Any look written for this picture is kept, whether or not ComfyUI answers.
    try:
        positive, people = scene.for_model()
        data = await images.render(positive, images.SCENE_SIZE, people, kind="scenes",
                                   **_explicit(cast, s))
    except images.ImagesUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    p = Picture(session_id=s.id, after_message_id=s.messages[-1].id, path=images.save(data), prompt=scene.text)
    db.add(p)
    db.commit()
    return picture_out(p)


def _texter(s: ChatSession) -> Character | None:
    """Who the selfie is from: whoever sent the latest reply, else the first in the scene."""
    cast = group.cast(s)
    last = next((m for m in reversed(s.messages) if m.role == "assistant"), None)
    if last is not None and last.speaker_id:
        found = next((c for c in cast if c.id == last.speaker_id), None)
        if found:
            return found
    return cast[0] if cast else None


def selfie_wanted(s: ChatSession, db: DbSession) -> bool:
    """In a text thread, the user asked for a picture and has had a reply but no picture yet."""
    from ..config import settings

    if s.mode != "texting" or not settings.texting_selfies or not (settings.comfyui_url or "").strip():
        return False
    msgs = [m for m in s.messages if m.role in ("user", "assistant")]
    if len(msgs) < 2 or msgs[-1].role != "assistant" or msgs[-2].role != "user":
        return False
    if not images.asks_for_picture(msgs[-2].content):
        return False
    return not db.query(Picture).filter(Picture.session_id == s.id,
                                        Picture.after_message_id >= msgs[-1].id).first()


@router.post("/sessions/{session_id}/selfie")
async def selfie(session_id: int, db: DbSession = Depends(get_db)) -> dict:
    """A phone selfie from whoever's texting, placed after the latest message."""
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    who = _texter(s)
    if who is None or not s.messages:
        raise HTTPException(400, "There's nobody to send a selfie yet")
    refused = images.not_pictured([who])
    if refused:
        raise HTTPException(400, refused)
    user = s.persona.name if s.persona else "You"
    recent = [(user if m.role == "user" else group.speaker_name(s, m), m.content)
              for m in s.messages if m.role in ("user", "assistant")][-8:]
    autonomy.ACTIVE += 1
    try:
        scene = await images.selfie_prompt(s, who, user, recent)
    except images.ImagesUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        autonomy.ACTIVE -= 1
    db.commit()
    try:
        positive, _ = scene.for_model()
        data = await images.render(positive, images.SELFIE_SIZE, kind="selfies",
                                   **_explicit([who], s))
    except images.ImagesUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    p = Picture(session_id=s.id, after_message_id=s.messages[-1].id, path=images.save(data), prompt=scene.text)
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
    refused = images.not_pictured([c])
    if refused:
        raise HTTPException(400, refused)
    autonomy.ACTIVE += 1
    try:
        prompt = await images.portrait_prompt(c)
    except images.ImagesUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        autonomy.ACTIVE -= 1
    db.commit()
    try:
        data = await images.render(prompt, images.PORTRAIT_SIZE, kind="portraits", **_explicit([c]))
    except images.ImagesUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    old = c.avatar_path
    c.avatar_path = images.save(data)
    db.commit()
    if old and os.path.exists(old) and old != c.avatar_path:
        os.remove(old)
    return {"id": c.id, "has_avatar": True, "prompt": prompt}
