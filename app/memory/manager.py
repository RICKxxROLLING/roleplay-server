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

from ..cards.models import CharacterCard, LoreEntry
from ..config import settings
from ..db.models import Character, ChatSession, Message
from ..prompt.tokens import estimate_tokens
from . import lorebook, rag
from .summarizer import summarize, update_facts


#: Context spent on pinned moments. A pin is a whole message, so a handful fit;
#: past this the oldest pins drop out and the inspector reports them.
PINNED_BUDGET_TOKENS = 600


@dataclass
class MemoryContext:
    history: list[tuple[str, str]]
    summary: str = ""
    #: The shared world's description, the setting the card sits in.
    world: str = ""
    facts: str = ""
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
    #: Pinned messages already condensed into the summary, speaker-labelled.
    pinned: list[str] = field(default_factory=list)
    pinned_dropped: int = 0

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
        exclude_ids: set[int] | None = None,
        speaker: Character | None = None,
    ) -> MemoryContext:
        """Compose the three memory sources into one context.

        `exclude_ids` leaves messages out of the history -- a swipe regenerates
        the last reply, so the reply being replaced must not be in its prompt.

        Async because retrieval has to embed the query, which is a network call.
        `db` is optional so callers that only want summary + lorebook (and have
        no session handy) can skip retrieval entirely.
        """
        from ..chat import group  # chat imports memory; import late

        msgs = [m for m in self.pending(session) if m.id not in (exclude_ids or ())]
        # `speaker` is who writes this reply in a group chat; others' lines are
        # labelled by name (chat/group.py). None is the session's character.
        history = group.history_roles(session, msgs, speaker)

        # Lorebook scans recent turns only, so it runs on pending history.
        # The speaker's world: members of a group need not share one.
        owner = speaker or session.character
        world = owner.world if owner else None
        world_entries = [LoreEntry.model_validate(e) for e in (world.entries or [])] if world else []
        lore = lorebook.select(card, history, world_entries=world_entries)

        ctx = MemoryContext(
            history=history,
            summary=session.summary or "",
            world=(world.description or "").strip() if world else "",
            facts=(session.facts or "") if settings.facts_enabled else "",
            lore_before=lorebook.render(lore.before_char),
            lore_after=lorebook.render(lore.after_char),
            lore_fired=[_label(e) for e in lore.all],
            lore_dropped=lore.dropped,
        )

        # Pins only matter once folded: a pending pin is already in the history
        # verbatim. Seen live: asked about a porch scene the summary had
        # skipped, the character invented one -- a pin is the user's way to
        # keep a moment the fold might drop.
        user_name = session.persona.name if session.persona else "You"
        char_name = card.name or "Character"
        names = {c.id: c.name for c in group.cast(session)}

        def who(m: Message) -> str:
            return user_name if m.role == "user" else group.speaker_name(session, m, names)

        mark = session.summarized_upto_id or 0
        pins = [m for m in session.messages
                if m.pinned and m.id <= mark and m.id not in (exclude_ids or ())]
        kept, used = [], 0
        for m in reversed(pins):  # newest first under the budget...
            line = f"{who(m)}: {m.content.strip()}"
            cost = estimate_tokens(line)
            if used + cost > PINNED_BUDGET_TOKENS:
                ctx.pinned_dropped += 1
                continue
            kept.append(line)
            used += cost
        ctx.pinned = kept[::-1]  # ...then shown in the order they happened

        if db is not None and settings.retrieval_enabled:
            # Anything still pending is about to be sent verbatim, so retrieving
            # it would only buy a duplicate; a pin is already in the prompt too.
            result = await rag.retrieve(
                db, session, history,
                exclude_ids={m.id for m in msgs} | {m.id for m in pins},
            )
            by_id = {m.id: m for m in session.messages}
            ctx.retrieved = rag.render(
                result.hits, char_name, user_name,
                speakers={h.message_id: who(by_id[h.message_id])
                          for h in result.hits if h.message_id in by_id},
            )
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

        from ..chat import group

        user_name = session.persona.name if session.persona else "You"
        # Folded from the session character's side; the others keep their names.
        turns = group.history_roles(session, foldable)

        new_summary = await summarize(session.summary or "", turns, card, user_name)

        # Only advance the watermark if the summary actually changed. Otherwise
        # the model failed and we'd be dropping turns into a black hole.
        if new_summary and new_summary != (session.summary or ""):
            session.summary = new_summary
            session.summarized_upto_id = foldable[-1].id
            # In lockstep with the summary: only turns the summary absorbed are
            # folded into the facts, so a rejected fold retries both together.
            # Best-effort -- a failed fact pass keeps the old list, the fold stands.
            if settings.facts_enabled:
                try:
                    session.facts = await update_facts(
                        session.facts or "", turns, card, user_name
                    )
                except Exception:
                    pass
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
