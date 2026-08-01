"""Memory Manager -- the seam every later phase plugs into.

Phase 1:         return full history; the prompt builder trims to fit.
Phase 2:         fold old turns into ChatSession.summary behind a watermark.
Phase 3:         keyword-match card.character_book, return triggered entries.
Phase 4 (now):   vector-retrieve relevant old turns.

The watermark (`ChatSession.summarized_upto_id`) is the core idea: messages at or
below it have been folded into the summary and are no longer sent verbatim.
Everything above it is recent history. This is deliberately *not* driven by the
prompt builder's `dropped_messages`, because that count is a per-turn budget
artefact that changes as samplers and card size change -- summarisation needs a
stable, monotonic boundary it can commit to.

That same watermark is what makes retrieval cheap to scope: everything below it
has left the prompt, so it is exactly the set worth searching.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session as DbSession

from ..cards.models import CharacterCard
from ..config import settings
from ..db.models import ChatSession, Message
from ..prompt.tokens import estimate_tokens
from . import lorebook, rag
from .summarizer import summarize


@dataclass
class MemoryContext:
    history: list[tuple[str, str]]
    summary: str = ""
    lore_before: list[str] = field(default_factory=list)
    lore_after: list[str] = field(default_factory=list)
    #: Entry names/keys that fired, for the inspector.
    lore_fired: list[str] = field(default_factory=list)
    lore_dropped: int = 0
    #: Rendered, speaker-labelled retrieval hits, ready for the prompt.
    retrieved: list[str] = field(default_factory=list)
    #: The same hits with ids and scores, for the inspector.
    retrieved_hits: list[rag.Hit] = field(default_factory=list)
    retrieval_dropped: int = 0
    retrieval_error: str | None = None

    @property
    def lore(self) -> list[str]:
        return [*self.lore_before, *self.lore_after]


def _label(entry) -> str:
    """Human-readable name for the inspector. Constant entries have no keys,
    so they'd otherwise all read as '(unnamed)'."""
    if entry.name:
        return entry.name
    if entry.keys:
        return ", ".join(entry.keys)
    if entry.constant:
        return "(always on)"
    return "(unnamed)"


class MemoryManager:
    # --- reads -------------------------------------------------------------

    def pending(self, session: ChatSession) -> list[Message]:
        """Messages not yet folded into the summary."""
        mark = session.summarized_upto_id or 0
        return [m for m in session.messages if m.id > mark]

    async def build_context(
        self,
        session: ChatSession,
        card: CharacterCard,
        db: DbSession | None = None,
    ) -> MemoryContext:
        """Compose the three memory sources into one context.

        Async because retrieval has to embed the query, which is a network call.
        `db` is optional so callers that only want summary + lorebook (and have
        no session handy) can skip retrieval entirely.
        """
        msgs = self.pending(session)
        history = [(m.role, m.content) for m in msgs]

        # Lorebook scans recent turns only, so it runs on pending history.
        lore = lorebook.select(card, history)

        ctx = MemoryContext(
            history=history,
            summary=session.summary or "",
            lore_before=lorebook.render(lore.before_char),
            lore_after=lorebook.render(lore.after_char),
            lore_fired=[_label(e) for e in lore.all],
            lore_dropped=lore.dropped,
        )

        if db is not None and settings.retrieval_enabled:
            # Anything still pending is about to be sent verbatim, so retrieving
            # it would only buy a duplicate.
            result = await rag.retrieve(
                db, session, history, exclude_ids={m.id for m in msgs}
            )
            user_name = session.persona.name if session.persona else "You"
            ctx.retrieved = rag.render(result.hits, card.name or "Character", user_name)
            ctx.retrieved_hits = result.hits
            ctx.retrieval_dropped = result.dropped
            ctx.retrieval_error = result.error

        return ctx

    # --- summarisation -----------------------------------------------------

    def _foldable(self, session: ChatSession) -> list[Message]:
        """The slice we'd fold right now: everything pending except the tail we
        always keep verbatim."""
        pending = self.pending(session)
        keep = max(1, settings.keep_recent_messages)
        if len(pending) <= keep:
            return []
        return pending[:-keep]

    def should_summarize(self, session: ChatSession) -> bool:
        if not settings.summary_enabled:
            return False
        foldable = self._foldable(session)
        if not foldable:
            return False
        pending_tokens = sum(estimate_tokens(m.content) for m in self.pending(session))
        return pending_tokens >= settings.summary_trigger_tokens

    async def summarize_now(
        self, db: DbSession, session: ChatSession, card: CharacterCard
    ) -> int:
        """Fold the eligible slice into the summary. Returns messages folded."""
        foldable = self._foldable(session)
        if not foldable:
            return 0

        user_name = session.persona.name if session.persona else "You"
        turns = [(m.role, m.content) for m in foldable]

        new_summary = await summarize(session.summary or "", turns, card, user_name)

        # Only advance the watermark if the summary actually changed. Otherwise
        # the model failed and we'd be dropping turns into a black hole.
        if new_summary and new_summary != (session.summary or ""):
            session.summary = new_summary
            session.summarized_upto_id = foldable[-1].id
            db.add(session)
            db.commit()
            db.refresh(session)
            return len(foldable)
        return 0

    async def after_turn(
        self, db: DbSession, session: ChatSession, card: CharacterCard
    ) -> int:
        if not self.should_summarize(session):
            return 0
        return await self.summarize_now(db, session, card)


memory_manager = MemoryManager()
