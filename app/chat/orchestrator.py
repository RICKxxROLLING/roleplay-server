"""Per-turn flow: load state -> build context -> build prompt -> stream -> persist -> fold.

Yields *typed events* rather than raw strings so the transport can tell the UI
about things that aren't tokens -- notably memory compression, which blocks for
a few seconds and would otherwise look like a hung stream.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from sqlalchemy.orm import Session as DbSession

from ..cards.models import CharacterCard
from ..config import settings
from ..db.models import ChatSession, Message
from ..llm import GenerationParams, get_client
from ..memory import memory_manager, rag
from ..prompt import build_prompt
from .guard import slipped_into_first_person, strip_user_actions


def _card_of(session: ChatSession) -> CharacterCard:
    return CharacterCard.model_validate(session.character.card or {})


async def build_turn_prompt(
    session: ChatSession,
    overrides: dict | None = None,
    db: DbSession | None = None,
    exclude_ids: set[int] | None = None,
):
    card = _card_of(session)
    ctx = await memory_manager.build_context(session, card, db=db, exclude_ids=exclude_ids)
    persona = session.persona
    return card, ctx, build_prompt(
        card=card,
        history=ctx.history,
        user_name=persona.name if persona else "You",
        user_persona=persona.description if persona else "",
        summary=ctx.summary,
        facts=ctx.facts,
        lore_before=ctx.lore_before,
        lore_after=ctx.lore_after,
        retrieved=ctx.retrieved,
        pinned=ctx.pinned,
        context_tokens=(overrides or {}).get("context_tokens", settings.context_tokens),
        max_new_tokens=(overrides or {}).get("max_new_tokens", settings.max_new_tokens),
        reserve_tokens=settings.reserve_tokens,
        prompt_format=settings.prompt_format,
        narration_style=settings.narration_style,
    )


def _params(built_stop: list[str], overrides: dict | None) -> GenerationParams:
    o = overrides or {}
    return GenerationParams(
        temperature=o.get("temperature", settings.temperature),
        top_p=o.get("top_p", settings.top_p),
        top_k=o.get("top_k", settings.top_k),
        min_p=o.get("min_p", settings.min_p),
        repeat_penalty=o.get("repeat_penalty", settings.repeat_penalty),
        repeat_last_n=o.get("repeat_last_n", settings.repeat_last_n),
        context_tokens=o.get("context_tokens", settings.context_tokens),
        max_new_tokens=o.get("max_new_tokens", settings.max_new_tokens),
        stop=built_stop,
    )


def _without_label(text: str, card: CharacterCard) -> str:
    """Models sometimes echo the speaker label despite priming; strip it."""
    reply = text.strip()
    prefix = f"{card.name}:"
    return reply[len(prefix) :].strip() if reply.startswith(prefix) else reply


def _guarded(reply: str, session: ChatSession, card: CharacterCard) -> tuple[str, int]:
    if not settings.guard_user_actions:
        return reply, 0
    user_name = session.persona.name if session.persona else "You"
    return strip_user_actions(reply, user_name, card.name)


def _persist(
    db: DbSession, session: ChatSession, reply: str, swipe_of: Message | None
) -> bool:
    """Save `reply` as a new message or as another swipe. False if empty."""
    if not reply:
        return False
    if swipe_of is not None:
        add_swipe(swipe_of, reply)
    else:
        db.add(Message(session_id=session.id, role="assistant", content=reply))
    db.commit()
    return True


#: Oldest swipes beyond this are dropped; each is a full reply held in the row.
MAX_SWIPES = 20


def add_swipe(message: Message, reply: str) -> None:
    """Append `reply` as the newest swipe and select it."""
    alts = list(message.alternatives or [message.content])
    alts.append(reply)
    alts = alts[-MAX_SWIPES:]
    message.alternatives = alts
    message.alt_index = len(alts) - 1
    message.content = reply


async def stream_reply(
    db: DbSession,
    session: ChatSession,
    overrides: dict | None = None,
    swipe_of: Message | None = None,
) -> AsyncIterator[dict]:
    """Stream the character's next reply, persist it, then update memory.

    With `swipe_of` (the session's last reply) the new text is added to that
    message as another swipe instead of becoming a new message, and the reply
    being replaced is left out of the prompt.
    """
    card, ctx, built = await build_turn_prompt(
        session, overrides, db=db, exclude_ids={swipe_of.id} if swipe_of else None
    )
    if ctx.retrieval_error:
        yield {
            "type": "memory",
            "status": "retrieval_failed",
            "message": ctx.retrieval_error,
        }
    elif ctx.retrieved:
        yield {
            "type": "memory",
            "status": "recalled",
            "recalled": len(ctx.retrieved),
        }

    client = get_client()

    collected: list[str] = []
    try:
        # Two attempts at most: a slipped narrator gets one redo, and the second
        # result is kept whatever it is -- a loop here could spin on a card that
        # just reads as first person.
        for attempt in range(2):
            collected = []
            async for piece in client.generate_stream(built.text, _params(built.stop, overrides)):
                collected.append(piece)
                yield {"type": "token", "text": piece}

            reply = _without_label("".join(collected), card)
            if (
                attempt == 0
                and settings.redo_first_person
                and slipped_into_first_person(reply, card.first_mes)
            ):
                # Tells the UI to drop the draft it has streamed so far.
                yield {"type": "redo", "reason": "first_person"}
                continue
            break
    except (asyncio.CancelledError, GeneratorExit):
        # Stop. The disconnect has already ended generation on the GPU (the
        # stream to the backend closes with this generator); keep what was
        # written so it can be edited, continued or swiped rather than lost.
        # No awaits from here: a closing generator may not suspend again.
        partial, _removed = _guarded(_without_label("".join(collected), card), session, card)
        _persist(db, session, partial, swipe_of)
        raise

    # Before persisting: once one reply narrating the user is in history, the
    # model copies it -- see chat/guard.py. The UI reloads the stored message
    # when the turn ends, so the streamed draft is replaced by this one.
    reply, removed = _guarded(reply, session, card)
    if removed:
        yield {"type": "guard", "removed": removed}

    if not _persist(db, session, reply, swipe_of):
        return
    db.refresh(session)

    # --- Phase 4: embed both sides of the turn ---
    # Deliberately *after* the reply is persisted rather than before generation:
    # this way one batched call covers the user turn and the character's, and no
    # message is left unindexed. Nothing is lost by waiting -- retrieval embeds
    # the query text directly, and a message this recent is still verbatim in
    # context, so it could not have been a search candidate anyway.
    if settings.retrieval_enabled:
        try:
            await rag.index_session(db, session)
        except Exception as exc:
            # Best-effort: the reply is already saved, and the next turn (or an
            # explicit reindex) picks up whatever this pass missed.
            yield {
                "type": "memory",
                "status": "index_failed",
                "message": f"{type(exc).__name__}: {exc}",
            }

    # --- Phase 2: fold old turns into the rolling summary ---
    if memory_manager.should_summarize(session):
        yield {"type": "memory", "status": "compressing"}
        try:
            folded = await memory_manager.summarize_now(db, session, card)
            yield {"type": "memory", "status": "done", "folded": folded}
        except Exception as exc:
            # A failed fold must never break the chat -- the turn is already
            # saved, and the watermark stays put so we simply retry next turn.
            yield {
                "type": "memory",
                "status": "failed",
                "message": f"{type(exc).__name__}: {exc}",
            }
