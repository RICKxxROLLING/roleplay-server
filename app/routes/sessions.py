from __future__ import annotations

import json
from typing import AsyncIterator, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..cards.models import CharacterCard
from ..chat import build_turn_prompt, group, stream_reply
from ..config import settings
from ..db import Character, ChatSession, Message, Persona, SessionLocal, SessionMember, get_db
from ..db.models import Notification
from ..memory import memory_manager, rag
from ..prompt import estimate_tokens, substitute, tokens

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionIn(BaseModel):
    character_id: int
    persona_id: int | None = None
    title: str | None = None
    greeting_index: int = 0  # 0 = first_mes, 1+ = alternate_greetings
    # Group chat: other characters in the scene. The greeting stays the main one's.
    member_ids: list[int] = []


class MessageIn(BaseModel):
    content: str
    # Optional per-request sampler overrides from the settings panel.
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    repeat_penalty: float | None = None
    max_new_tokens: int | None = None
    # Group chat: who answers. Unset lets the server choose (chat/group.py).
    speaker_id: int | None = None

    def overrides(self) -> dict:
        return {k: v for k, v in self.model_dump(exclude={"content", "speaker_id"}).items()
                if v is not None}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _serialise(m: Message, s: ChatSession | None = None) -> dict:
    s = s or m.session
    return {
        "id": m.id,
        "role": m.role,
        "speaker_id": group.speaker_id(s, m) if m.role == "assistant" else None,
        "speaker_name": group.speaker_name(s, m) if m.role == "assistant" else None,
        "content": m.content,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "pinned": bool(m.pinned),
        "initiated": bool(m.initiated),
        "texted": bool(m.texted),
        "swipes": len(m.alternatives) if m.alternatives else 1,
        "swipe_index": m.alt_index if m.alternatives else 0,
    }


@router.post("")
def create_session(body: SessionIn, db: DbSession = Depends(get_db)) -> dict:
    char = db.get(Character, body.character_id)
    if not char:
        raise HTTPException(404, "Character not found")
    persona = db.get(Persona, body.persona_id) if body.persona_id else None
    if body.persona_id and not persona:
        raise HTTPException(404, "Persona not found")

    members = _members(db, char.id, body.member_ids)
    names = " & ".join([char.name, *(c.name for c in members)])
    s = ChatSession(
        character_id=char.id,
        persona_id=persona.id if persona else None,
        title=body.title or f"Chat with {names}",
    )
    db.add(s)
    db.commit()
    s.members = [SessionMember(character_id=c.id, position=i) for i, c in enumerate(members)]
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

    return {"id": s.id, "title": s.title, "messages": [_serialise(m, s) for m in s.messages]}


def _members(db: DbSession, owner_id: int, ids: list[int]) -> list[Character]:
    out, seen = [], {owner_id}
    for cid in ids:
        if cid in seen:
            continue
        c = db.get(Character, cid)
        if not c:
            raise HTTPException(404, f"Character {cid} not found")
        out.append(c)
        seen.add(cid)
    return out


def _cast(s: ChatSession) -> list[dict]:
    return [{"id": c.id, "name": c.name, "has_avatar": bool(c.avatar_path)}
            for c in group.cast(s)]


class MembersIn(BaseModel):
    member_ids: list[int]


@router.put("/{session_id}/members")
def set_members(session_id: int, body: MembersIn, db: DbSession = Depends(get_db)) -> dict:
    """Who else is in the scene. Lines by a character who leaves keep their name."""
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    members = _members(db, s.character_id, body.member_ids)
    s.members = [SessionMember(character_id=c.id, position=i) for i, c in enumerate(members)]
    db.commit()
    db.refresh(s)
    return {"cast": _cast(s)}


@router.get("")
def list_sessions(db: DbSession = Depends(get_db)) -> list[dict]:
    rows = db.query(ChatSession).order_by(ChatSession.id.desc()).all()
    return [
        {
            "id": s.id,
            "title": s.title,
            "character_id": s.character_id,
            "character_name": s.character.name if s.character else None,
            "member_names": [c.name for c in group.cast(s)[1:]],
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
        "scene": s.scene,
        "cast": _cast(s),
        **_autonomy_status(s),
        "messages": [_serialise(m, s) for m in s.messages],
    }


def _stream_response(
    session_id: int, overrides: dict, swipe_of_id: int | None = None,
    speaker_id: int | None = None,
) -> StreamingResponse:
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
            swipe_of = db.get(Message, swipe_of_id) if swipe_of_id else None
            speaker = group.by_id(s, speaker_id) if speaker_id else None
            # The orchestrator already emits typed events; pass them straight through.
            async for event in stream_reply(db, s, overrides, swipe_of=swipe_of,
                                            speaker=speaker):
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
    if body.speaker_id is not None and not group.by_id(s, body.speaker_id):
        raise HTTPException(400, "That character is not in this chat")

    # Marked like the replies, so a thread renders as one phone conversation.
    db.add(Message(session_id=s.id, role="user", content=body.content.strip(),
                   texted=s.mode == "texting"))
    db.commit()
    return _stream_response(session_id, body.overrides(), speaker_id=body.speaker_id)


class RespondIn(BaseModel):
    character_id: int | None = None


@router.post("/{session_id}/respond")
def respond(session_id: int, body: RespondIn, db: DbSession = Depends(get_db)) -> StreamingResponse:
    """A character speaks next without a new user message -- in a group, one
    answering another. Unset picks as for a user message."""
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    if body.character_id is not None and not group.by_id(s, body.character_id):
        raise HTTPException(400, "That character is not in this chat")
    return _stream_response(session_id, {}, speaker_id=body.character_id)


@router.post("/{session_id}/regenerate")
def regenerate(session_id: int, db: DbSession = Depends(get_db)) -> StreamingResponse:
    """Re-roll the last reply as a new swipe; earlier versions are kept.

    The reply is not deleted first, so a failed or empty generation leaves it
    exactly as it was.
    """
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    last = s.messages[-1] if s.messages else None
    # The greeting is a reply too, and swiping it is how a chat re-rolls its
    # opening -- but only while it is the whole chat.
    swipe = last.id if last is not None and last.role == "assistant" else None
    return _stream_response(session_id, {}, swipe_of_id=swipe)


class SwipeIn(BaseModel):
    index: int


@router.post("/{session_id}/messages/{message_id}/swipe")
def select_swipe(
    session_id: int, message_id: int, body: SwipeIn, db: DbSession = Depends(get_db)
) -> dict:
    """Show another version of the last reply."""
    s = db.get(ChatSession, session_id)
    m = db.get(Message, message_id)
    if not s or not m or m.session_id != session_id:
        raise HTTPException(404, "Message not found")
    # Only the latest reply: once answered, the reply has shaped the turns after
    # it, and swapping it underneath them would make the history incoherent.
    if s.messages[-1].id != m.id or not m.alternatives:
        raise HTTPException(400, "Only the latest reply can be swiped")
    if not 0 <= body.index < len(m.alternatives):
        raise HTTPException(400, "No such swipe")
    m.alt_index = body.index
    m.content = m.alternatives[body.index]
    db.commit()
    return _serialise(m, s)


@router.get("/{session_id}/prompt")
async def inspect_prompt(
    session_id: int, speaker_id: int | None = None, db: DbSession = Depends(get_db)
) -> dict:
    """Debug view -- exactly what the model will receive."""
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    last_user = next((m.content for m in reversed(s.messages) if m.role == "user"), "")
    speaker = (group.by_id(s, speaker_id) if speaker_id else None) \
        or group.choose_speaker(s, last_user)
    # One build, not two: retrieval embeds the query, so calling build_context
    # again here would double the round-trips just to report on them.
    _card, ctx, built = await build_turn_prompt(s, db=db, speaker=speaker)
    return {
        "speaker": speaker.name if speaker else None,
        "prompt": built.text,
        "stop": built.stop,
        "estimated_tokens": built.used_tokens,
        "chars_per_token": round(tokens.chars_per_token()[0], 2),
        "chars_per_token_measured": tokens.chars_per_token()[1],
        "dropped_messages": built.dropped_messages,
        "lore_entries": built.lore_entries,
        "lore_fired": ctx.lore_fired,
        "lore_dropped": ctx.lore_dropped,
        "pinned_entries": len(ctx.pinned),
        "pinned_dropped": ctx.pinned_dropped,
        "retrieved_entries": built.retrieved_entries,
        "retrieved": [
            {
                "message_id": h.message_id,
                "role": h.role,
                "score": round(h.score, 4),
                "content": h.content,
            }
            for h in ctx.retrieved_hits
        ],
        "retrieval_dropped": ctx.retrieval_dropped,
        "retrieval_error": ctx.retrieval_error,
    }


class SessionPatch(BaseModel):
    title: str | None = None
    persona_id: int | None = None
    # persona_id=None is ambiguous (unset vs. "play as nobody"), so clearing is
    # an explicit flag rather than a magic value.
    clear_persona: bool = False
    #: Let the character write first (chat/autonomy.py).
    autonomous: bool | None = None
    #: "story" or "texting" (chat/texting.py).
    mode: Literal["story", "texting"] | None = None


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

    if body.autonomous is not None:
        s.autonomous = body.autonomous
        s.next_check_at = None
    if body.mode is not None:
        s.mode = body.mode

    db.commit()
    db.refresh(s)
    return {"id": s.id, "title": s.title, "persona_id": s.persona_id, **_autonomy_status(s)}


def _autonomy_status(s: ChatSession) -> dict:
    from ..chat import autonomy

    return {
        "mode": s.mode or "story",
        "autonomous": bool(s.autonomous),
        "unanswered": autonomy.unanswered(s),
        "suspended": autonomy.suspended(s),
        "max_unanswered": settings.autonomy_max_unanswered,
        # Why the next check would not write, if it wouldn't -- for the chat header.
        "waiting_because": autonomy.eligible(s) if s.autonomous else None,
    }


class WriteFirstIn(BaseModel):
    #: Skip the timing rules and the "would they?" pass -- the "Message me now" button.
    force: bool = False


@router.post("/{session_id}/write-first")
async def write_first(session_id: int, body: WriteFirstIn, db: DbSession = Depends(get_db)) -> dict:
    """Run one writing-first check of this chat now, as the scheduler would."""
    from ..chat import autonomy

    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    if not s.messages:
        raise HTTPException(400, "Say something first")
    return await autonomy.check(db, s, force=body.force)


class MessagePatch(BaseModel):
    content: str | None = None
    pinned: bool | None = None


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
    s = db.get(ChatSession, session_id)
    if body.pinned is not None:
        m.pinned = body.pinned
    if body.content is None:
        db.commit()
        return {"id": m.id, "content": m.content, "pinned": m.pinned,
                "below_watermark": False, "note": None}
    content = body.content.strip()
    if not content:
        raise HTTPException(400, "Message cannot be empty")

    m.content = content
    if m.alternatives:
        # The edit belongs to the swipe on screen; without this, flipping away
        # and back would bring the unedited text back.
        alts = list(m.alternatives)
        alts[m.alt_index] = content
        m.alternatives = alts
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
    summary: str | None = None
    facts: str | None = None


@router.get("/{session_id}/memory")
def get_memory(session_id: int, db: DbSession = Depends(get_db)) -> dict:
    """Inspect the rolling summary and what's still verbatim."""
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    pending = memory_manager.pending(s)
    index = rag.index_state(db, s)
    return {
        "summary": s.summary or "",
        "facts": s.facts or "",
        "facts_enabled": settings.facts_enabled,
        "summarized_upto_id": s.summarized_upto_id or 0,
        "summarized_count": len(s.messages) - len(pending),
        "pending_count": len(pending),
        "pending_tokens": sum(estimate_tokens(m.content) for m in pending),
        "trigger_tokens": settings.summary_trigger_tokens,
        "will_summarize_next_turn": memory_manager.should_summarize(s),
        "retrieval_enabled": settings.retrieval_enabled,
        "embedding_model": index["model"],
        "indexed_count": index["indexed"],
        "unindexed_count": index["stale"],
        "embeddable_count": index["embeddable"],
        # Retrieval only searches what has left the prompt, so with nothing
        # folded there is nothing for it to find. Worth saying plainly in the UI.
        "searchable_count": len(s.messages) - len(pending),
    }


class ProbeIn(BaseModel):
    query: str
    limit: int = 20


@router.post("/{session_id}/retrieve")
async def probe_retrieval(
    session_id: int, body: ProbeIn, db: DbSession = Depends(get_db)
) -> dict:
    """Score arbitrary text against this session's memory, without generating.

    The calibration tool for `retrieval_min_score`. Unlike the prompt
    inspector, it shows results that fall *below* the floor -- you cannot pick
    a threshold from data that was filtered out before you saw it.
    """
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    if not body.query.strip():
        raise HTTPException(400, "Query cannot be empty")

    # Same exclusion as a real turn: pending messages are already verbatim in
    # the prompt, so a probe that returned them would misrepresent the live path.
    pending = {m.id for m in memory_manager.pending(s)}
    result = await rag.probe(db, s, body.query, exclude_ids=pending, limit=body.limit)

    return {
        "query": body.query,
        "candidates": result.candidates,
        "error": result.error,
        "settings": {
            "embedding_model": settings.embedding_model,
            "min_score": settings.retrieval_min_score,
            "top_k": settings.retrieval_top_k,
            "budget_tokens": settings.retrieval_budget_tokens,
        },
        "results": [
            {
                "message_id": p.message_id,
                "role": p.role,
                "score": round(p.score, 4),
                "would_inject": p.would_inject,
                "rejected_by": p.rejected_by,
                "content": p.content,
            }
            for p in result.results
        ],
    }


@router.post("/{session_id}/reindex")
async def reindex(session_id: int, db: DbSession = Depends(get_db)) -> dict:
    """Embed any message without a current vector.

    Normally this happens on each turn, so it's here for the two cases that
    can't wait: a chat that predates retrieval being switched on, and a change
    of embedding model, which invalidates every stored vector at once.
    """
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    try:
        written = await rag.index_session(db, s)
    except Exception as exc:
        raise HTTPException(502, f"Embedding backend failed: {type(exc).__name__}: {exc}")
    return {"indexed": written, **rag.index_state(db, s)}


@router.patch("/{session_id}/memory")
def patch_memory(
    session_id: int, body: MemoryPatch, db: DbSession = Depends(get_db)
) -> dict:
    """Hand-edit the summary. Summarisation is lossy, so being able to correct
    a bad fold is the difference between usable and infuriating."""
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    if body.summary is not None:
        s.summary = body.summary
    if body.facts is not None:
        s.facts = body.facts
    db.add(s)
    db.commit()
    return {"ok": True, "summary": s.summary, "facts": s.facts}


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
    # Waiting notifications about it would open a chat that no longer exists.
    db.query(Notification).filter(Notification.session_id == session_id).delete()
    db.delete(s)
    db.commit()
    return {"ok": True}


class ScenePatch(BaseModel):
    location: str | None = None
    time: str | None = None
    weather: str | None = None
    present: str | None = None


@router.patch("/{session_id}/scene")
def patch_scene(session_id: int, body: ScenePatch, db: DbSession = Depends(get_db)) -> dict:
    """Correct the scene by hand; the next update starts from the correction."""
    from ..memory.scene import blank

    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    user = s.persona.name if s.persona else "You"
    scene = dict(s.scene or blank(s.character.name if s.character else "Character", user))
    for key, value in body.model_dump(exclude_none=True).items():
        scene[key] = value.strip()[:80] or "unknown"
    s.scene = scene
    db.commit()
    return {"scene": s.scene}
