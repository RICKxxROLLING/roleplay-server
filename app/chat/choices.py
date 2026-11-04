"""Choose your own adventure: three things the user could do next, instead of typing.

Asked for after a reply rather than as part of it, so the reply isn't slowed and a choice
isn't in the character's history. Never written to the chat until the user picks one, and
then it's an ordinary user message. Kept on the session against the message it answers, so
reopening a chat shows the same three.

Prototyped on Stheno over 32 sets. Asked for "three different things" it wrote mostly three
lines of speech; asked for one thing said, one done and one change of direction it kept to
that, in about 1.5 s. It also runs long and gets cut off by the token cap, and writes the
action half without the *asterisks* the chat uses, so the server trims and marks up.

Romance is never offered unless everyone in the scene has it allowed (memory/trust.py). The
owner can always type whatever they like, but a button shouldn't suggest it, and with a
minor in the scene it must never appear.
"""
from __future__ import annotations

import hashlib
import re

from ..cards.models import CharacterCard
from ..config import settings
from ..db.models import ChatSession
from ..llm import GenerationParams, get_client
from ..memory import trust
from ..prompt.formats import get_format
from . import group

COUNT = 3
ATTEMPTS = 3
MAX_CHARS = 170

STORY = """{people} and {user} are in an ongoing story.
{setting}
The story so far:
{transcript}

Write {user}'s next move three different ways, one short line each, in first person as {user} would write it, for {user} to choose from:

1. {user} says something, in quotes.
2. {user} does something, starting with "I".
3. {user} changes the direction of the scene: asks about something new, goes somewhere, or brings something up."""

TEXTING = """{people} and {user} are texting each other.
{setting}
The conversation so far:
{transcript}

Write three different texts {user} could send next, one short line each, as {user} would write them, for {user} to choose from:

1. A reply to the last message.
2. A question.
3. A change of subject."""

_LINE = re.compile(r"^\s*(?:\d[.)]|[-*])?\s*(.+?)\s*$")
_NUMBERED = re.compile(r"^\s*\d[.)]\s*")
_ENDS = re.compile(r"[.!?…][\"”*]*(?=\s|$)")
_ROMANCE = re.compile(
    r"\b(kiss\w*|lips?|caress\w*|embrac\w*|cuddl\w*|seduc\w*|naked|nude|undress\w*|strip\w*|bed|beds|bedroom|"
    r"lover\w*|sex\w*|moan\w*|breasts?|thighs?|waist|hips?|neck|romantic\w*|romance|love you|date|"
    r"flirt\w*|tease|sensual\w*|intimate\w*|arous\w*|desire\w*|hold you|stroke\w*|hand on your)\b", re.I)


def _speech_and_action(line: str) -> str:
    """The chat's own format: narration in *asterisks*, speech in quotes."""
    line = line.replace("*", "").strip()
    pieces = re.split(r'("[^"]*"|“[^”]*”)', line)
    out = []
    for piece in pieces:
        piece = piece.strip(" ,")
        if not piece:
            continue
        out.append(piece if piece[0] in '"“' else f"*{piece}*")
    return " ".join(out)


def _trim(line: str) -> str:
    """Whole sentences only, up to the cap: a cut-off choice reads worse than a short one."""
    if line.count('"') % 2:
        # A quote that never closes was cut off. Keep what comes before it, if anything.
        line = line[:line.rfind('"')].rstrip(" ,")
    if len(line) <= MAX_CHARS and _ENDS.search(line[-3:] + " "):
        return line
    ends = [m.end() for m in _ENDS.finditer(line[:MAX_CHARS]) if line[:m.end()].count('"') % 2 == 0]
    return line[:ends[-1]].strip() if ends else ""


def parse(raw: str, names: list[str], romance_ok: bool, texting: bool = False) -> list[str]:
    """Usable choices, best first. Names are the user and the cast's own, to catch a line
    that has slipped out of first person or into writing someone else's turn."""
    out: list[str] = []
    for row in raw.splitlines():
        if not _NUMBERED.match(row) and out:
            # Text after a numbered line that isn't numbered itself is the model running on.
            continue
        m = _LINE.match(row)
        if not m:
            continue
        line = _trim(_NUMBERED.sub("", m.group(1)).strip())
        if len(line) < 4 or "{{" in line:
            continue
        if not romance_ok and _ROMANCE.search(line):
            continue
        if any(re.search(rf"(?<!\w){re.escape(n)}\s+(says|asks|replies|nods|smiles|laughs)\b", line) for n in names if n):
            continue
        text = line.strip('"“”') if texting else _speech_and_action(line)
        if text.lower() not in (o.lower() for o in out):
            out.append(text)
    return out[:COUNT]


def _romance_ok(session: ChatSession) -> bool:
    people = group.cast(session)
    return bool(people) and all(
        trust.romance_allowed(CharacterCard.model_validate(c.card or {"name": c.name})) for c in people)


def _key(session: ChatSession) -> str:
    last = session.messages[-1]
    return f"{last.id}:{hashlib.sha1(last.content.encode()).hexdigest()[:12]}"


def build_prompt(session: ChatSession) -> str:
    fmt = get_format(settings.prompt_format)
    user = session.persona.name if session.persona else "You"
    people = " and ".join(c.name for c in group.cast(session)) or "the characters"
    world = session.character.world if session.character else None
    recent = [m for m in session.messages if m.role in ("user", "assistant", group.NARRATOR_ROLE)][-4:]
    transcript = "\n".join(
        f"{user if m.role == 'user' else group.speaker_name(session, m)}: {m.content[:600]}" for m in recent)
    body = (TEXTING if session.mode == "texting" else STORY).format(
        people=people, user=user, transcript=transcript,
        setting=f"Setting: {world.description[:500]}" if world and world.description else "",
    )
    return fmt.user.format(text=body) + fmt.joiner + fmt.response_open + "1."


async def offer(db, session: ChatSession, refresh: bool = False) -> list[str]:
    """Choices for the chat as it stands. Empty when it's the user's turn to have spoken,
    or when nothing usable came back: the type box still works."""
    if not session.messages or session.messages[-1].role == "user":
        return []
    key = _key(session)
    kept = session.choices or {}
    if not refresh and kept.get("key") == key:
        return kept.get("options") or []
    fmt = get_format(settings.prompt_format)
    params = GenerationParams(
        temperature=0.9, top_p=0.95, top_k=50, min_p=0.05, repeat_penalty=1.05,
        context_tokens=settings.context_tokens, max_new_tokens=150, stop=list(fmt.stops),
    )
    user = session.persona.name if session.persona else "You"
    names = [user, *(c.name for c in group.cast(session))]
    romance_ok = _romance_ok(session)
    prompt = build_prompt(session)
    best: list[str] = []
    for _ in range(ATTEMPTS):
        got = parse("1." + await get_client().generate(prompt, params), names, romance_ok,
                    session.mode == "texting")
        if len(got) > len(best):
            best = got
        if len(best) == COUNT:
            break
    session.choices = {"key": key, "options": best}
    db.commit()
    return best
