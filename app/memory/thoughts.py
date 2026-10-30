"""A character's own thoughts: a private journal, and what they think of the user.

After each reply, a short extra generation (like the scene tracker) asks the
speaking character for two things:

- **A journal entry**, in their own voice -- what just happened and how they feel
  about it, the things they wouldn't say aloud. Kept for the Journal panel only.
- **A view**: one line, third person, "Privately, Dina ...", their current
  opinion of the user. This is what later prompts read.

Two outputs because they do different jobs. The journal is the character
speaking for themselves -- first person is the point of it. But the prompt must
not carry first person: this project's own measurements show a narrator drifts
into first person once any of it sits in context (chat/guard.py), so what goes
back in is the third-person view only.

Each view is written from the previous one plus the latest exchange, so it
changes gradually -- the base the next Phase 3 item (earned trust) builds on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import settings
from ..llm import GenerationParams, get_client
from ..prompt.formats import ALL_MARKERS, get_format

# Measured on Stheno over two live transcripts. The first wording let every pass
# escalate the last: Dina's view went "stabilizing anchor" -> "beacon of hope" ->
# "revere" -> "North Star" in seven ordinary exchanges, and the journal said "I
# loved him" over a mug of cocoa. It also lost whose journal it was: Garrick's
# first entry was written as Riley ("my new master"). Hence: whose journal, in
# plain words, and an opinion that moves only as far as the exchange moved it.
THOUGHTS_PROMPT = """You are {char}, writing in your own private journal about {user}. \
This just happened between you:

{exchange}

What you ({char}) privately thought of {user} until now: {view}

Write two things.

Journal: two or three plain, honest sentences in {char}'s own voice -- what just \
happened and how {char} feels about {user} now. The things {char} wouldn't say out loud.
View: one sentence, under 30 words, starting "Privately, {char}" -- {char}'s current \
opinion of {user}, in third person. Most moments change an opinion only a little, or \
not at all: keep what still holds, and change only what this exchange actually changed."""

_VIEW = re.compile(r"^\s*\**View\**\s*:\s*(.+)$", re.I | re.M)


@dataclass
class Thought:
    journal: str
    view: str


def _clean(text: str) -> str:
    for m in ALL_MARKERS:
        text = text.replace(m, "")
    return re.sub(r"\s+", " ", text).strip().strip('"').strip()


def parse_thought(raw: str, char_name: str) -> Thought | None:
    """Both parts, or None: a half-answer is not worth storing."""
    m = _VIEW.search(raw or "")
    if not m:
        return None
    journal = _clean(raw[: m.start()].replace("Journal:", "", 1))
    view = _clean(m.group(1).split("\n")[0])
    first = (char_name or "").split(" ")[0]
    if len(journal) < 30 or len(journal) > 1200:
        return None
    # The view goes into every later prompt: it must be about the character's
    # opinion, in third person, and short.
    if not view.lower().startswith("privately") or (first and first not in view):
        return None
    if re.search(r"\b(I|me|my)\b", view) or len(view) > 300:
        return None
    return Thought(journal=journal, view=view)


def build_thoughts_prompt(user_text: str, reply: str, char: str, user: str, view: str) -> str:
    fmt = get_format(settings.prompt_format)
    exchange = f"{user}: {user_text.strip()[-800:]}\n{char}: {reply.strip()[-1500:]}"
    body = THOUGHTS_PROMPT.format(char=char, user=user, exchange=exchange,
                                  view=view or "nothing much yet -- you are still forming an opinion")
    return fmt.user.format(text=body) + fmt.joiner + fmt.response_open + "Journal:"


async def think(user_text: str, reply: str, char: str, user: str, view: str) -> Thought | None:
    params = GenerationParams(
        temperature=0.8, top_p=0.95, top_k=40, repeat_penalty=1.05,
        context_tokens=settings.context_tokens, max_new_tokens=160,
        stop=list(get_format(settings.prompt_format).stops),
    )
    raw = await get_client().generate(build_thoughts_prompt(user_text, reply, char, user, view), params)
    return parse_thought("Journal:" + raw, char)


# --- storage ------------------------------------------------------------------------


def latest_view(db, session_id: int, character_id: int, exclude_message_ids=()) -> str:
    """The character's current opinion of the user in this chat, or ""."""
    from ..db.models import CharacterThought

    q = (db.query(CharacterThought)
         .filter(CharacterThought.session_id == session_id,
                 CharacterThought.character_id == character_id)
         .order_by(CharacterThought.id.desc()))
    for t in q:
        if t.message_id not in set(exclude_message_ids or ()):
            return t.view
    return ""


async def after_reply(db, session, speaker, reply_message, user_text: str, user_name: str) -> bool:
    """Write the speaker's journal entry for `reply_message`. A swipe of that
    message replaces its entry. Best-effort: False when nothing usable came back."""
    from ..db.models import CharacterThought

    prev = latest_view(db, session.id, speaker.id, exclude_message_ids={reply_message.id})
    thought = await think(user_text or f"(nothing -- {speaker.name} wrote first)",
                          reply_message.content, speaker.name, user_name, prev)
    if thought is None:
        return False
    db.query(CharacterThought).filter(CharacterThought.message_id == reply_message.id).delete()
    db.add(CharacterThought(session_id=session.id, character_id=speaker.id,
                            message_id=reply_message.id, journal=thought.journal, view=thought.view))
    db.commit()
    return True
