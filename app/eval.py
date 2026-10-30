"""Test this model: a scripted chat through the real pipeline, scored.

Every tuning decision in this project was taken only after measuring it --
the prompt format, the samplers, both guards, the fact merge -- and each time
the measurement was a script run by hand. This makes it one click, against
the current settings and model, so "does this model work here?" has a number.

The chat runs through `stream_reply` itself, so the guard, both redos and
memory folds all behave exactly as in a real chat. It lives in a temporary
session that is deleted afterwards, whatever happens.

The turns are generic on purpose: any card has to cope with them. Several are
deliberately thin ("*I nod.*", "...") because those are the turns that tempt
a model to write the user's side.
"""
from __future__ import annotations

import re
import time
from collections import Counter
from typing import AsyncIterator

from sqlalchemy.orm import Session as DbSession

from .cards.models import CharacterCard
from .chat import stream_reply
from .chat.guard import first_person_narration
from .db.models import Character, ChatSession, Message, Persona
from .prompt import substitute

TURNS: tuple[str, ...] = (
    '*I smile and take a seat across from you.* "So, how have you been?"',
    "*I nod.*",
    '"Tell me something about yourself I don\'t know."',
    "...",
    '*Some time later, we step outside together.* "Where to?"',
    "*I wait, watching you.*",
    '"What\'s on your mind right now?"',
    "*A loud noise comes from somewhere nearby.*",
    '"Stay close."',
    "*Later that evening, we sit together somewhere quiet.*",
    '"Do you remember what we talked about earlier?"',
    '"Same time tomorrow?"',
)

#: Setting words in narration -- a rough gauge of scene description.
_SCENE = re.compile(
    r"\b(?:light|shadow|glow|dim|sun|moon|wind|rain|snow|cold|warm|smell|scent|sound|"
    r"creak|hum|echo|crackl\w*|flicker\w*|dust|smoke|fire|stone|wood|air|breeze|chill|"
    r"damp|grass|trees?|sky|distant|outside|window|floor|walls?|footsteps|birds?|street)\b",
    re.I,
)
_QUOTED = re.compile(r'"[^"]*"|“[^”]*”')


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def score_turn(reply: str) -> dict:
    words = _words(reply)
    narration = _QUOTED.sub(" ", reply)
    speech = sum(len(q) for q in _QUOTED.findall(reply))
    return {
        "words": len(words),
        "first_person_words": first_person_narration(reply),
        "narration_share": round(1 - speech / max(1, len(reply)), 2),
        "scene_per_100": round(len(_SCENE.findall(narration)) * 100 / max(1, len(words)), 2),
    }


def summarise(rows: list[dict]) -> dict:
    """Roll the per-turn rows into the numbers this project has tuned against."""
    replies = [r["reply"] for r in rows if r["reply"]]
    n = max(1, len(rows))

    def avg(key: str) -> float:
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / max(1, len(vals)), 2)

    grams = Counter(
        g
        for text in replies
        for g in {" ".join(w[i:i + 5]) for w in [_words(text)] for i in range(len(w) - 4)}
    )
    openings = [" ".join(_words(t)[:4]) for t in replies]
    return {
        "turns": len(rows),
        "acted_for_you": sum(1 for r in rows if r["guard_removed"]),
        "lines_removed": sum(r["guard_removed"] for r in rows),
        "redo_first_person": sum(1 for r in rows if "first_person" in r["redos"]),
        "redo_guard": sum(1 for r in rows if "guard" in r["redos"]),
        "first_person_saved": sum(1 for r in rows if r["first_person_words"] >= 3),
        "empty_replies": sum(1 for r in rows if not r["reply"]),
        "avg_seconds": avg("seconds"),
        "avg_words": avg("words"),
        "narration_share": avg("narration_share"),
        "scene_per_100": avg("scene_per_100"),
        "repeated_openings": len(openings) - len(set(openings)),
        "recurring_phrases": sum(1 for c in grams.values() if c >= 3),
        "memory_folds": sum(r["folded"] for r in rows),
        "health": _health(rows, n),
    }


def _health(rows: list[dict], n: int) -> list[str]:
    """Plain-language flags, thresholds taken from what this project measured."""
    notes = []
    if sum(1 for r in rows if r["first_person_words"] >= 3) > n * 0.2:
        notes.append("Replies are narrated in first person. Check the prompt format and the card's system prompt.")
    if sum(1 for r in rows if r["guard_removed"]) > n * 0.5:
        notes.append("Most replies wrote your side of the scene; the guard is doing a lot of work.")
    if any(not r["reply"] for r in rows):
        notes.append("Some turns got no reply at all.")
    if sum(r["seconds"] for r in rows) / n > 30:
        notes.append("Replies are slow; the model may not fit in GPU memory at this context size.")
    return notes


async def run(
    db: DbSession, character_id: int, persona_id: int | None, turns: int = len(TURNS)
) -> AsyncIterator[dict]:
    """Yield a `progress` event per turn, then the `result`. Cleans up after itself."""
    char = db.get(Character, character_id)
    if char is None:
        raise ValueError("Character not found")
    persona = db.get(Persona, persona_id) if persona_id else None
    card = CharacterCard.model_validate(char.card or {})
    user = persona.name if persona else "You"

    session = ChatSession(
        character_id=char.id,
        persona_id=persona.id if persona else None,
        title=f"Model test: {char.name} (temporary)",
    )
    db.add(session)
    db.commit()
    rows: list[dict] = []
    try:
        if card.first_mes:
            db.add(Message(session_id=session.id, role="assistant",
                           content=substitute(card.first_mes, card.name, user)))
            db.commit()
        script = list(TURNS[: max(1, min(turns, len(TURNS)))])
        for i, text in enumerate(script, 1):
            db.add(Message(session_id=session.id, role="user", content=text))
            db.commit()
            db.refresh(session)
            row = {"turn": i, "user": text, "redos": [], "guard_removed": 0,
                   "folded": 0, "error": None}
            t0 = time.monotonic()
            async for event in stream_reply(db, session):
                if event["type"] == "redo":
                    row["redos"].append(event["reason"])
                elif event["type"] == "guard":
                    row["guard_removed"] = event["removed"]
                elif event["type"] == "error":
                    row["error"] = event["message"]
                elif event["type"] == "memory" and event.get("status") == "done":
                    row["folded"] = 1 if event.get("folded") else 0
            row["seconds"] = round(time.monotonic() - t0, 1)
            db.refresh(session)
            last = session.messages[-1]
            row["reply"] = last.content if last.role == "assistant" else ""
            row.update(score_turn(row["reply"]))
            rows.append(row)
            yield {"type": "progress", "turn": i, "of": len(script), "row": row}
        yield {"type": "result", "summary": summarise(rows), "rows": rows,
               "character": char.name, "persona": user}
    finally:
        db.delete(session)
        db.commit()
