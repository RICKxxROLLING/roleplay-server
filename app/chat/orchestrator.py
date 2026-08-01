"""Per-turn flow: load state -> build context -> build prompt -> stream -> persist -> fold.

Yields *typed events* rather than raw strings so the transport can tell the UI
about things that aren't tokens -- notably memory compression, which blocks for
a few seconds and would otherwise look like a hung stream.
"""
from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.orm import Session as DbSession

from ..cards.models import CharacterCard
from ..config import settings
from ..db.models import ChatSession, Message
from ..llm import GenerationParams, get_client
from ..memory import memory_manager, rag
from ..prompt import build_prompt


def _card_of(session: ChatSession) -> CharacterCard:
    return CharacterCard.model_validate(session.character.card or {})


async def build_turn_prompt(
    session: ChatSession,
    overrides: dict | None = None,
    db: DbSession | None = None,
):
    card = _card_of(session)
    ctx = await memory_manager.build_context(session, card, db=db)
    persona = session.persona
    return card, ctx, build_prompt(
        card=card,
        history=ctx.history,
        user_name=persona.name if persona else "You",
        user_persona=persona.description if persona else "",
        summary=ctx.summary,
        lore_before=ctx.lore_before,
        lore_after=ctx.lore_after,
        retrieved=ctx.retrieved,
        context_tokens=(overrides or {}).get("context_tokens", settings.context_tokens),
        max_new_tokens=(overrides or {}).get("max_new_tokens", settings.max_new_tokens),
        reserve_tokens=settings.reserve_tokens,
    )


def _params(built_stop: list[str], overrides: dict | None) -> GenerationParams:
    o = overrides or {}
    return GenerationParams(
        temperature=o.get("temperature", settings.temperature),
        top_p=o.get("top_p", settings.top_p),
        top_k=o.get("top_k", settings.top_k),
        repeat_penalty=o.get("repeat_penalty", settings.repeat_penalty),
        max_new_tokens=o.get("max_new_tokens", settings.max_new_tokens),
        stop=built_stop,
    )


async def stream_reply(
    db: DbSession,
    session: ChatSession,
    overrides: dict | None = None,
) -> AsyncIterator[dict]:
    """Stream the character's next reply, persist it, then update memory."""
    card, ctx, built = await build_turn_prompt(session, overrides, db=db)
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
    async for piece in client.generate_stream(built.text, _params(built.stop, overrides)):
        collected.append(piece)
        yield {"type": "token", "text": piece}

    reply = "".join(collected).strip()
    # Models sometimes echo the speaker label despite priming; strip it.
    prefix = f"{card.name}:"
    if reply.startswith(prefix):
        reply = reply[len(prefix) :].strip()

    if not reply:
        return

    db.add(Message(session_id=session.id, role="assistant", content=reply))
    db.commit()
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
