from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..cards.models import CharacterCard
from ..chat import build_turn_prompt, stream_reply
from ..config import settings
from ..db import Character, ChatSession, Message, Persona, SessionLocal, get_db
from ..memory import memory_manager
from ..prompt import estimate_tokens, substitute

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionIn(BaseModel):
    character_id: int
    persona_id: int | None = None
    title: str | None = None
    greeting_index: int = 0  # 0 = first_mes, 1+ = alternate_greetings


class MessageIn(BaseModel):
    content: str
    # Optional per-request sampler overrides from the settings panel.
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    repeat_penalty: float | None = None
    max_new_tokens: int | None = None

    def overrides(self) -> dict:
        return {k: v for k, v in self.model_dump(exclude={"content"}).items() if v is not None}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _serialise(m: Message) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.post("")
def create_session(body: SessionIn, db: DbSession = Depends(get_db)) -> dict:
    char = db.get(Character, body.character_id)
    if not char:
        raise HTTPException(404, "Character not found")
    persona = db.get(Persona, body.persona_id) if body.persona_id else None
    if body.persona_id and not persona:
        raise HTTPException(404, "Persona not found")

    s = ChatSession(
        character_id=char.id,
        persona_id=persona.id if persona else None,
        title=body.title or f"Chat with {char.name}",
    )
    db.add(s)
    db.commit()
    db.refresh(s)

    # Seed the opening message from the card.
    card = CharacterCard.model_validate(char.card or {})
    greetings = [card.first_mes, *card.alternate_greetings]
    idx = body.greeting_index if 0 <= body.greeting_index < len(greetings) else 0
    greeting = greetings[idx] if greetings else ""
    if greeting:
        db.add(
            Message(
                session_id=s.id,
                role="assistant",
                content=substitute(greeting, card.name, persona.name if persona else "You"),
            )
        )
        db.commit()
        db.refresh(s)

    return {"id": s.id, "title": s.title, "messages": [_serialise(m) for m in s.messages]}


@router.get("")
def list_sessions(db: DbSession = Depends(get_db)) -> list[dict]:
    rows = db.query(ChatSession).order_by(ChatSession.id.desc()).all()
    return [
        {
            "id": s.id,
            "title": s.title,
            "character_id": s.character_id,
            "character_name": s.character.name if s.character else None,
            "message_count": len(s.messages),
        }
        for s in rows
    ]


@router.get("/{session_id}")
def get_session(session_id: int, db: DbSession = Depends(get_db)) -> dict:
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return {
        "id": s.id,
        "title": s.title,
        "character_id": s.character_id,
        "character_name": s.character.name if s.character else None,
        "persona_id": s.persona_id,
        "summary": s.summary,
        "messages": [_serialise(m) for m in s.messages],
    }


def _stream_response(session_id: int, overrides: dict) -> StreamingResponse:
    """Open a *fresh* DB session inside the generator.

    The request-scoped session from Depends() is closed as soon as the handler
    returns, which happens before the generator body runs.
    """

    async def gen() -> AsyncIterator[str]:
        db = SessionLocal()
        try:
            s = db.get(ChatSession, session_id)
            if not s:
                yield _sse({"type": "error", "message": "Session not found"})
                return
            # The orchestrator already emits typed events; pass them straight through.
            async for event in stream_reply(db, s, overrides):
                yield _sse(event)
            yield _sse({"type": "done"})
        except Exception as exc:  # surface backend failures to the UI
            yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            db.close()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{session_id}/messages")
def send_message(
    session_id: int, body: MessageIn, db: DbSession = Depends(get_db)
) -> StreamingResponse:
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    if not body.content.strip():
        raise HTTPException(400, "Message cannot be empty")

    db.add(Message(session_id=s.id, role="user", content=body.content.strip()))
    db.commit()
    return _stream_response(session_id, body.overrides())


@router.post("/{session_id}/regenerate")
def regenerate(session_id: int, db: DbSession = Depends(get_db)) -> StreamingResponse:
    """Simple regenerate: drop the trailing assistant turn and re-roll it."""
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    if s.messages and s.messages[-1].role == "assistant":
        db.delete(s.messages[-1])
        db.commit()
    return _stream_response(session_id, {})


@router.get("/{session_id}/prompt")
def inspect_prompt(session_id: int, db: DbSession = Depends(get_db)) -> dict:
    """Debug view -- exactly what the model will receive."""
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    card, built = build_turn_prompt(s)
    ctx = memory_manager.build_context(s, card)
    return {
        "prompt": built.text,
        "stop": built.stop,
        "estimated_tokens": built.used_tokens,
        "dropped_messages": built.dropped_messages,
        "lore_entries": built.lore_entries,
        "lore_fired": ctx.lore_fired,
        "lore_dropped": ctx.lore_dropped,
    }


class SessionPatch(BaseModel):
    title: str | None = None
    persona_id: int | None = None
    # persona_id=None is ambiguous (unset vs. "play as nobody"), so clearing is
    # an explicit flag rather than a magic value.
    clear_persona: bool = False


@router.patch("/{session_id}")
def update_session(
    session_id: int, body: SessionPatch, db: DbSession = Depends(get_db)
) -> dict:
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")

    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(400, "Title cannot be empty")
        s.title = title

    if body.clear_persona:
        s.persona_id = None
    elif body.persona_id is not None:
        if not db.get(Persona, body.persona_id):
            raise HTTPException(404, "Persona not found")
        s.persona_id = body.persona_id

    db.commit()
    db.refresh(s)
    return {"id": s.id, "title": s.title, "persona_id": s.persona_id}


class MessagePatch(BaseModel):
    content: str


@router.patch("/{session_id}/messages/{message_id}")
def edit_message(
    session_id: int,
    message_id: int,
    body: MessagePatch,
    db: DbSession = Depends(get_db),
) -> dict:
    m = db.get(Message, message_id)
    if not m or m.session_id != session_id:
        raise HTTPException(404, "Message not found")
    content = body.content.strip()
    if not content:
        raise HTTPException(400, "Message cannot be empty")

    s = db.get(ChatSession, session_id)
    m.content = content
    db.commit()

    # Below the watermark the original text is already baked into the summary,
    # so the edit won't reach the model. Say so rather than failing silently.
    below = message_id <= (s.summarized_upto_id or 0)
    return {
        "id": m.id,
        "content": m.content,
        "below_watermark": below,
        "note": (
            "This message is already condensed into the summary, so editing it "
            "won't change what the model sees. Edit the summary instead."
            if below
            else None
        ),
    }


@router.delete("/{session_id}/messages/{message_id}")
def delete_message(
    session_id: int, message_id: int, db: DbSession = Depends(get_db)
) -> dict:
    m = db.get(Message, message_id)
    if not m or m.session_id != session_id:
        raise HTTPException(404, "Message not found")
    s = db.get(ChatSession, session_id)
    below = message_id <= (s.summarized_upto_id or 0)
    db.delete(m)
    db.commit()
    return {"ok": True, "below_watermark": below}


class MemoryPatch(BaseModel):
    summary: str


@router.get("/{session_id}/memory")
def get_memory(session_id: int, db: DbSession = Depends(get_db)) -> dict:
    """Inspect the rolling summary and what's still verbatim."""
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    pending = memory_manager.pending(s)
    return {
        "summary": s.summary or "",
        "summarized_upto_id": s.summarized_upto_id or 0,
        "summarized_count": len(s.messages) - len(pending),
        "pending_count": len(pending),
        "pending_tokens": sum(estimate_tokens(m.content) for m in pending),
        "trigger_tokens": settings.summary_trigger_tokens,
        "will_summarize_next_turn": memory_manager.should_summarize(s),
    }


@router.patch("/{session_id}/memory")
def patch_memory(
    session_id: int, body: MemoryPatch, db: DbSession = Depends(get_db)
) -> dict:
    """Hand-edit the summary. Summarisation is lossy, so being able to correct
    a bad fold is the difference between usable and infuriating."""
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    s.summary = body.summary
    db.add(s)
    db.commit()
    return {"ok": True, "summary": s.summary}


@router.post("/{session_id}/summarize")
async def force_summarize(session_id: int, db: DbSession = Depends(get_db)) -> dict:
    """Trigger a fold immediately, ignoring the token threshold."""
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    card = CharacterCard.model_validate(s.character.card or {})
    folded = await memory_manager.summarize_now(db, s, card)
    return {"folded": folded, "summary": s.summary}


@router.delete("/{session_id}")
def delete_session(session_id: int, db: DbSession = Depends(get_db)) -> dict:
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    db.delete(s)
    db.commit()
    return {"ok": True}
