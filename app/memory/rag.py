"""Vector retrieval over past turns (Phase 4).

The problem this solves is the one summarisation is structurally worst at:
precise recall of a detail from long ago. "What was the innkeeper's name in
chapter one" is exactly the sort of fact a rolling summary drops on its second
fold. Retrieval is a *third* source alongside the summary and the lorebook, not
a replacement for either -- the summary carries narrative flow, the lorebook
carries authored canon, and this carries verbatim specifics.

Two decisions worth understanding before changing anything here.

**Storage is float32 blobs scanned linearly, not sqlite-vec.** An extension
means `enable_load_extension`, which isn't compiled into every Python build, and
a virtual table, which the additive-only migration helper in `db/database.py`
cannot express. At single-user scale the scan is not the bottleneck: 2000
vectors x 768 dims scores in ~65ms of pure Python on this machine, against an
embedding round-trip of a similar order and a generation that takes seconds.
`retrieval_max_candidates` bounds the worst case. If a chat ever grows past
where that hurts, this module is the only thing that has to change.

**Only messages the prompt is *not* already carrying verbatim are searched.**
Retrieving a turn that is about to appear in the history block anyway spends
budget to say the same thing twice, and duplicated text measurably encourages
models to repeat themselves. In practice that means candidates are the messages
below the summarisation watermark -- so retrieval and summarisation are
complements: with `summary_enabled` off nothing is ever folded, and retrieval
correctly finds nothing to add.
"""
from __future__ import annotations

import array
import hashlib
import sys
from dataclasses import dataclass, field
from operator import mul

from sqlalchemy.orm import Session as DbSession

from ..config import settings
from ..db.models import ChatSession, Message, MessageEmbedding
from ..llm.factory import get_embedder
from ..prompt.tokens import estimate_tokens

#: Messages per embedding request. Large enough that indexing a backlog is a few
#: round-trips, small enough not to build a multi-megabyte JSON body.
BATCH = 32


@dataclass
class Hit:
    message_id: int
    role: str
    content: str
    score: float


@dataclass
class RetrievalResult:
    hits: list[Hit] = field(default_factory=list)
    #: Scored above the threshold but didn't fit the token budget or top-k.
    dropped: int = 0
    candidates: int = 0
    #: Set when the embedding backend failed. Retrieval degrades to nothing
    #: rather than breaking the turn, so this is the only trace it left.
    error: str | None = None


# --- vector plumbing -------------------------------------------------------


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pack(vector: list[float]) -> bytes:
    """Normalise to unit length and serialise as little-endian float32.

    Normalising at write time turns every later cosine similarity into a plain
    dot product, which is the whole reason the linear scan is affordable.
    """
    arr = array.array("f", vector)
    norm = sum(map(mul, arr, arr)) ** 0.5
    if norm:
        arr = array.array("f", [v / norm for v in arr])
    if sys.byteorder != "little":  # pragma: no cover - x86/ARM are little-endian
        arr.byteswap()
    return arr.tobytes()


def unpack(blob: bytes) -> array.array:
    arr = array.array("f")
    arr.frombytes(blob)
    if sys.byteorder != "little":  # pragma: no cover
        arr.byteswap()
    return arr


def similarity(a: array.array, b: array.array) -> float:
    """Cosine similarity of two already-normalised vectors."""
    if len(a) != len(b):
        return 0.0
    return sum(map(mul, a, b))


# --- indexing --------------------------------------------------------------


def _stale(msg: Message, row: MessageEmbedding | None, model: str) -> bool:
    """Whether this message needs (re)embedding.

    A vector from a different embedding model shares no coordinate space with
    the current one, and a vector of text the user has since edited describes
    something that is no longer there. Both must be recomputed, not compared.
    """
    if row is None:
        return True
    return row.model != model or row.content_hash != content_hash(msg.content)


def _vectors_by_message(db: DbSession, session: ChatSession) -> dict[int, MessageEmbedding]:
    """One query for the whole session.

    Walking `Message.embedding` instead would emit a SELECT per message, and
    this runs on every turn and every open of the memory panel.
    """
    rows = (
        db.query(MessageEmbedding)
        .filter(MessageEmbedding.session_id == session.id)
        .all()
    )
    return {r.message_id: r for r in rows}


def unindexed(
    db: DbSession, session: ChatSession, model: str | None = None
) -> list[Message]:
    model = model or settings.embedding_model
    existing = _vectors_by_message(db, session)
    return [
        m
        for m in session.messages
        if m.content.strip() and _stale(m, existing.get(m.id), model)
    ]


async def index_session(db: DbSession, session: ChatSession) -> int:
    """Embed every message of `session` that lacks a current vector.

    Runs once per completed turn rather than at each `Message` insert: it covers
    both halves of the exchange in one batched call, backfills chats that
    predate this phase, and repairs edited messages -- none of which a
    write-time hook at each insert site would do.
    """
    model = settings.embedding_model
    existing = _vectors_by_message(db, session)
    pending = [
        m
        for m in session.messages
        if m.content.strip() and _stale(m, existing.get(m.id), model)
    ]
    if not pending:
        return 0

    embedder = get_embedder()
    written = 0
    for start in range(0, len(pending), BATCH):
        chunk = pending[start : start + BATCH]
        vectors = await embedder.embed([m.content for m in chunk])
        if len(vectors) != len(chunk):
            raise RuntimeError(
                f"Embedder returned {len(vectors)} vectors for {len(chunk)} inputs"
            )
        for msg, vector in zip(chunk, vectors):
            row = existing.get(msg.id)
            blob = pack(vector)
            if row is None:
                db.add(
                    MessageEmbedding(
                        message_id=msg.id,
                        session_id=session.id,
                        model=model,
                        dim=len(vector),
                        content_hash=content_hash(msg.content),
                        vector=blob,
                    )
                )
            else:
                row.model = model
                row.dim = len(vector)
                row.content_hash = content_hash(msg.content)
                row.vector = blob
            written += 1
        db.commit()

    db.refresh(session)
    return written


def index_state(db: DbSession, session: ChatSession) -> dict:
    """Coverage report for the memory inspector."""
    model = settings.embedding_model
    total = sum(1 for m in session.messages if m.content.strip())
    stale = len(unindexed(db, session, model))
    return {
        "embeddable": total,
        "indexed": total - stale,
        "stale": stale,
        "model": model,
    }


# --- retrieval -------------------------------------------------------------


def build_query(history: list[tuple[str, str]]) -> str:
    """The search query is the tail of the conversation, not just the last line.

    One message alone is often a pronoun-heavy fragment ("do you remember him?")
    that embeds to nothing useful; a few turns of context anchor it.
    """
    n = max(1, settings.retrieval_query_messages)
    return "\n".join(content for _, content in history[-n:] if content.strip())


async def retrieve(
    db: DbSession,
    session: ChatSession,
    history: list[tuple[str, str]],
    exclude_ids: set[int] | None = None,
) -> RetrievalResult:
    """Find past turns worth re-injecting for the current moment.

    Never raises: a retrieval failure must not cost the user their turn. The
    reason surfaces through `RetrievalResult.error` and the memory inspector.
    """
    if not settings.retrieval_enabled:
        return RetrievalResult()

    query = build_query(history)
    if not query.strip():
        return RetrievalResult()

    exclude = exclude_ids or set()
    model = settings.embedding_model

    rows = (
        db.query(MessageEmbedding)
        .filter(
            MessageEmbedding.session_id == session.id,
            MessageEmbedding.model == model,
        )
        .order_by(MessageEmbedding.message_id.desc())
        .limit(max(1, settings.retrieval_max_candidates))
        .all()
    )
    candidates = [r for r in rows if r.message_id not in exclude]
    if not candidates:
        return RetrievalResult()

    try:
        vectors = await get_embedder().embed([query])
    except Exception as exc:
        return RetrievalResult(error=f"{type(exc).__name__}: {exc}", candidates=len(candidates))
    if not vectors:
        return RetrievalResult(error="Embedder returned no vector", candidates=len(candidates))

    q = unpack(pack(vectors[0]))

    scored: list[tuple[float, MessageEmbedding]] = []
    for row in candidates:
        score = similarity(q, unpack(row.vector))
        if score >= settings.retrieval_min_score:
            scored.append((score, row))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    top_k = max(0, settings.retrieval_top_k)
    budget = settings.retrieval_budget_tokens
    hits: list[Hit] = []
    used = 0
    dropped = 0
    for score, row in scored:
        # Cheap rejections before the row fetch, so a permissive threshold
        # doesn't turn into one SELECT per candidate.
        if len(hits) >= top_k:
            dropped += 1
            continue
        msg = db.get(Message, row.message_id)
        if msg is None:  # embedding outlived its message
            continue
        cost = estimate_tokens(msg.content)
        if budget and used + cost > budget:
            dropped += 1
            continue
        used += cost
        hits.append(Hit(msg.id, msg.role, msg.content, score))

    # Selection is by score; presentation is chronological, so the injected
    # block reads as a fragment of the story rather than a ranked list.
    hits.sort(key=lambda h: h.message_id)
    return RetrievalResult(hits=hits, dropped=dropped, candidates=len(candidates))


def render(hits: list[Hit], char_name: str, user_name: str) -> list[str]:
    """Speaker-label each hit. Unattributed lines get read as narration and the
    model loses track of who said what."""
    out = []
    for h in hits:
        speaker = user_name if h.role == "user" else char_name
        text = h.content.strip()
        if text:
            out.append(f"{speaker}: {text}")
    return out
