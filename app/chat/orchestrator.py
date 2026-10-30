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
from ..memory.scene import update_scene
from ..prompt import build_prompt, tokens
from .guard import first_person_narration, slipped_into_first_person, strip_user_actions


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
        world=ctx.world,
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
        # A swipe replaces the last reply, so it starts from the scene before it.
        scene=(session.scene_prev if exclude_ids else session.scene)
        if settings.scene_enabled else None,
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


def _gutted(raw: str, kept: str) -> bool:
    """Whether the guard left too little of a reply to stand on its own.

    Measured over 288 Stheno replies: the guard touched 60, removing a median
    of 11%, and only one lost more than a third -- still 1,200 characters. So
    the share removed is the wrong trigger; what hurts is a reply left a stub
    or empty, which used to save nothing and leave the user's turn unanswered.
    Relative, not a character floor: a terse in-character reply is legitimate.
    """
    return not kept or len(kept) < len(raw) // 2


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

    # A chat whose last saved reply is already first person has drifted, and a
    # redo cannot pull it back -- the model copies the transcript. Seen live
    # after a double slip on turn one: the redo fired on every later turn,
    # doubling GPU time, and every reply stayed first person. Regenerating or
    # editing that reply is what recovers such a chat.
    prior = [m for m in session.messages
             if m.role == "assistant" and (swipe_of is None or m.id != swipe_of.id)]
    # prior[0] is the greeting, the reference itself, so it never counts as drift.
    drifted = len(prior) > 1 and slipped_into_first_person(
        prior[-1].content, card.first_mes
    )
    first_try: tuple[str, str, int] | None = None

    collected: list[str] = []
    try:
        # Two attempts at most, shared by both redo reasons, and the second
        # result is kept whatever it is -- a loop here could spin on a card that
        # just reads as first person.
        for attempt in range(2):
            collected = []
            client.last_prompt_tokens = None
            async for piece in client.generate_stream(built.text, _params(built.stop, overrides)):
                collected.append(piece)
                yield {"type": "token", "text": piece}
            tokens.observe(settings.model, len(built.text), client.last_prompt_tokens)

            raw = _without_label("".join(collected), card)
            # Before persisting: once one reply narrating the user is in
            # history, the model copies it -- see chat/guard.py.
            reply, removed = _guarded(raw, session, card)
            redo = None
            if (
                attempt == 0
                and settings.redo_first_person
                and not drifted
                and slipped_into_first_person(raw, card.first_mes)
            ):
                redo = "first_person"
                first_try = (raw, reply, removed)
            elif (
                attempt == 1
                and first_try is not None
                and first_person_narration(raw) > first_person_narration(first_try[0])
            ):
                # Both attempts slipped: keep whichever slipped less.
                raw, reply, removed = first_try
            elif attempt == 0 and removed and _gutted(raw, reply):
                redo = "guard"
            if redo:
                # Tells the UI to drop the draft it has streamed so far.
                yield {"type": "redo", "reason": redo}
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

    # The UI reloads the stored message when the turn ends, so the streamed
    # draft is replaced by the guarded one.
    if removed:
        yield {"type": "guard", "removed": removed}
    if not reply:
        # Everything the model wrote was the user's side of the scene. Say so
        # rather than leave the user's message silently unanswered.
        yield {"type": "error", "message": "The reply only narrated your own actions, so "
               "nothing was kept. Try Regenerate."}

    if not _persist(db, session, reply, swipe_of):
        return

    # --- Scene state: cheap, best-effort, and never allowed to break the turn ---
    if settings.scene_enabled:
        try:
            base = session.scene_prev if swipe_of is not None else session.scene
            user_text = next((m.content for m in reversed(session.messages)
                              if m.role == "user"), "")
            new_scene = await update_scene(
                base, user_text, reply, card,
                session.persona.name if session.persona else "You",
            )
            session.scene_prev = base
            session.scene = new_scene
            db.commit()
            yield {"type": "scene", "scene": new_scene}
        except Exception:
            pass
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
