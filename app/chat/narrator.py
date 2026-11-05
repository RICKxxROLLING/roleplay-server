"""The narrator: a short line of its own about the world around the scene.

Mostly quiet background, with a small everyday event every few lines. Stored as its
own message, role "narrator", written just before the character's reply: the reply
stays the chat's last message, so swipes work as ever, and the character can take it
up at once. Every prompt sees it as a labelled line on the user's side (chat/group.py).

Prototyped on Stheno against two live chats. Asked plainly for "weather, a sound,
someone passing", nearly every line was a threat ("a sudden bang", gunfire, a figure at
the door) and went on to write how the people reacted: the rushing owners complain of.
What fixed it, none of it optional:
  - "quiet, small, ordinary" as the request, with the server opening the sentence.
    Opening with "Outside," took it off "Suddenly," and "As Dina...".
  - A subject picked by the server. Left to choose, half the lines were clocks and crows.
  - The world's description back in: without it, jazz on the radio in a dead America.
  - `judge` afterwards, since the model proposes and the server disposes.
Quiet lines cost the scene nothing: over 24 replies one character noticed one. A visitor
gets a response every time and a chore about two times in three, so events are rationed.
"""
from __future__ import annotations

import random
import re

from ..config import settings
from ..db.models import Character, ChatSession, Message, SessionMember
from ..llm import GenerationParams, get_client
from ..prompt.formats import get_format
from . import group

ROLE = group.NARRATOR_ROLE
#: Every how many lines an event comes up: two quiet lines, then one that asks for a response.
EVENT_EVERY = 3
#: Most lines are one try. A refused line costs ~1 s, and posting nothing is harmless.
ATTEMPTS = 4
MAX_CHARS = 280

AMBIENT = [
    ("the wind", "Outside,"), ("the light", "Outside,"), ("birds", "Outside,"),
    ("woodsmoke", "Not far off,"), ("a horse", "Not far off,"),
    ("a dog", "Somewhere nearby,"), ("water dripping", "Somewhere nearby,"),
    ("a neighbor at a chore", "Across the way,"), ("a neighbor carrying something home", "Across the way,"),
]
EVENTS = [
    ("someone stops by with a small everyday request", "Before long,"),
    ("a chore makes itself known: something needs a minute's attention", "A little later,"),
    ("word arrives of some small piece of local news", "Around then,"),
]

_HEAD = """You are the narrator of an ongoing story about {people} and {user}.
{setting}
{where}

The story so far:
{transcript}

"""
AMBIENT_PROMPT = _HEAD + (
    "Write one short, quiet line of background about {subject}. It belongs to this setting and "
    "this time of day, it is small and ordinary, and nobody needs to act on it. It happens in the "
    "world around them, and the people in the story are not part of the line."
)
EVENT_PROMPT = _HEAD + (
    "Write one or two short sentences in which {subject}. It belongs to this setting and this "
    "time of day, it is minor and everyday, and nobody is in danger. Stop as soon as it has "
    "happened: how {people} and {user} respond is theirs to write."
)

_ALARM = re.compile(
    r"\b(sudden\w*|eerie|ominous\w*|startl\w*|gun\w*|shots?|scream\w*|bang\w*|crash\w*|howl\w*|"
    r"infected|shriek\w*|menac\w*|threat\w*|urgent\w*|frantic\w*|panic\w*|bullets?|"
    # Not alarming in themselves, but read as a plot hook. "A lone figure, hood up and
    # rifle slung low" was meant as background; live, Dina set about investigating it.
    r"figures?|silhouette\w*|rifles?|hood\w*|lurk\w*|stealth\w*|skulk\w*|sneak\w*)\b", re.I)
_THEM = re.compile(r"\b(both|they|their|them)\b", re.I)
#: A full stop after these doesn't end a sentence: "Across the way, Mrs." was seen live.
_SENTENCE = re.compile(r"(?<!\bMrs)(?<!\bMr)(?<!\bMs)(?<!\bDr)(?<!\bSt)[.!?]+[\"”]?(?=\s|$)")
_MORNING = re.compile(r"\b(dawn|sunrise|daybreak|morning)\b", re.I)
_EVENING = re.compile(r"\b(evening|dusk|sunset|twilight|night\w*|midnight|moon\w*)\b", re.I)
_STOPWORDS = {"the", "a", "an", "of", "and", "to", "in", "on", "as", "its", "with", "through", "from", "at", "it"}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower())) - _STOPWORDS


def _wrong_time(line: str, scene_time: str) -> bool:
    return bool((_MORNING.search(scene_time) and _EVENING.search(line))
                or (_EVENING.search(scene_time) and _MORNING.search(line)))


def judge(text: str, names: list[str], *, event: bool = False, scene_time: str = "",
          recent: list[str] | None = None) -> str:
    """The line to post, or "" to refuse it. Whole sentences only, since a line cut off
    by the token cap reads worse than none. Names cover the user and the cast: a line
    with them in it is writing their actions for them."""
    ends = [m.end() for m in _SENTENCE.finditer(text)]
    # An event's second sentence is where it starts talking or writing the cast, and
    # the first often stands well alone: live, 6 of 15 button presses found nothing.
    for end in ([ends[1], ends[0]] if event and len(ends) > 1 else ends[:1]):
        line = text[:end].strip()
        if _acceptable(line, names, event, scene_time, recent):
            return line
    return ""


def _acceptable(line: str, names: list[str], event: bool, scene_time: str, recent: list[str] | None) -> bool:
    if len(line) > MAX_CHARS or '"' in line or "”" in line or "*" in line or _ALARM.search(line):
        return False
    if any(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", line, re.I) for n in names if n):
        return False
    if not event and _THEM.search(line):
        return False
    if _wrong_time(line, scene_time):
        return False
    mine = _words(line)
    for old in recent or ():
        seen = _words(old)
        if seen and len(mine & seen) / max(1, min(len(mine), len(seen))) > 0.6:
            return False
    return True


def _lines(session: ChatSession) -> list[Message]:
    return [m for m in session.messages if m.role == ROLE]


def due(session: ChatSession) -> bool:
    """Whether a line goes in before this reply. Counted from the messages, so there is
    no counter to fall out of step when someone deletes one."""
    if not session.narrator or session.mode == "texting":
        return False
    if not session.messages or session.messages[-1].role != "user":
        return False
    since = 0
    for m in reversed(session.messages):
        if m.role == ROLE:
            break
        since += m.role == "user"
    # Arriving somewhere new is the other reason to speak, though not twice running.
    now, before = session.scene or {}, session.scene_prev or {}
    moved = now.get("location") and before.get("location") and now["location"] != before["location"]
    if moved and since >= 2:
        return True
    # Seeded, so the gap is steady across reloads but not metronomic across a chat.
    gap = settings.narrator_every + random.Random(f"{session.id}:{len(_lines(session))}").choice((-1, 0, 1))
    return since >= max(2, gap)


def _pick(session: ChatSession, count: int, event: bool) -> tuple[str, str]:
    """Each subject comes round once before any repeats, in an order all the chat's own."""
    pool = list(EVENTS if event else AMBIENT)
    random.Random(session.id).shuffle(pool)
    return pool[count % len(pool)]


def _world_extras(session: ChatSession) -> list[Character]:
    """People of this chat's world who could stop by and aren't in the scene."""
    world = group.world_of(session)
    here = {c.id for c in group.cast(session)}
    return [c for c in (world.characters if world else []) if getattr(c, "extra", False) and c.id not in here]


def _about(person: Character) -> str:
    text = ((person.card or {}).get("description") or "").strip()
    return f"About {person.name}: {text[:300]}" if text else ""


def build_prompt(session: ChatSession, event: bool, subject: str, opener: str, about: str = "") -> str:
    fmt = get_format(settings.prompt_format)
    user = session.persona.name if session.persona else "You"
    people = " and ".join(c.name for c in group.cast(session)) or "the characters"
    world = group.world_of(session)
    scene = session.scene or {}
    where = " ".join(f"{label}: {scene[key]}." for key, label in
                     (("location", "Where"), ("time", "Time"), ("weather", "Weather")) if scene.get(key))
    recent = [m for m in session.messages if m.role in ("user", "assistant", ROLE)][-6:]
    transcript = "\n".join(
        f"{user if m.role == 'user' else group.speaker_name(session, m)}: {m.content[:700]}" for m in recent
    )
    body = (EVENT_PROMPT if event else AMBIENT_PROMPT).format(
        people=people, user=user, subject=subject, where=where, transcript=transcript,
        setting=(f"Setting: {world.description[:900]}" if world and world.description else "")
        + (f"\n{about}" if about else ""),
    )
    return fmt.user.format(text=body) + fmt.joiner + fmt.response_open + opener


async def write_line(session: ChatSession, event: bool | None = None) -> str:
    """A line for this chat, or "" if every attempt was refused."""
    lines = _lines(session)
    count = len(lines)
    if event is None:
        # In a world chat the people come through Walk on: an unasked-for visitor can't be answered
        # by anyone, since they aren't the person the user is talking to.
        event = session.world_id is None and count % EVENT_EVERY == EVENT_EVERY - 1
    extras = _world_extras(session) if event else []
    params = GenerationParams(
        temperature=0.9, top_p=1, top_k=50, min_p=0.075, repeat_penalty=1.1,
        context_tokens=settings.context_tokens, max_new_tokens=70,
        stop=list(get_format(settings.prompt_format).stops),
    )
    user = session.persona.name if session.persona else "You"
    names = [user, *(c.name for c in group.cast(session))]
    # Someone with a card of their own would be described wrongly by a line that hasn't read
    # it: "Ellie pops her head in, her blonde pigtails askew." Extras are fine, as they're
    # the visitors, and the prompt is told who they are.
    world = group.world_of(session)
    names += [c.name for c in (world.characters if world else []) if not getattr(c, "extra", False)]
    names += [n.split()[0] for n in names if " " in n]
    client = get_client()
    for attempt in range(ATTEMPTS):
        # A new subject each try: repeated presses used to fail on the one this chat had
        # reached, however many times it was asked.
        subject, opener = _pick(session, count + attempt, event)
        # A kept extra is the visitor when there is one: a name the owner has seen and
        # chosen, rather than another invented Sarah.
        about = ""
        if extras and subject.startswith("someone "):
            person = extras[count % len(extras)]
            subject, about = person.name + subject[len("someone"):], _about(person)
        prompt = build_prompt(session, event, subject, opener, about)
        raw = await client.generate(prompt, params)
        line = judge(f"{opener}{raw}".rstrip(), names, event=event,
                     scene_time=str((session.scene or {}).get("time") or ""),
                     recent=[m.content for m in lines[-3:]])
        if line:
            return line
    return ""


async def post_line(db, session: ChatSession, event: bool | None = None) -> Message | None:
    line = await write_line(session, event)
    if not line:
        return None
    message = Message(session_id=session.id, role=ROLE, content=line)
    db.add(message)
    db.commit()
    db.refresh(session)
    return message


async def bring_in(db, session: ChatSession, person: Character) -> Message:
    """A guest arrives: a line from the narrator, and they join the scene. Always posts,
    since the owner asked for this person by name and a plain line reads fine."""
    fmt = get_format(settings.prompt_format)
    user = session.persona.name if session.persona else "You"
    names = [user, *(c.name for c in group.cast(session))]
    names += [n.split()[0] for n in names if " " in n]
    opener = "Before long,"
    prompt = build_prompt(session, True, f"{person.name} comes by", opener, _about(person))
    params = GenerationParams(
        temperature=0.9, top_p=1, top_k=50, min_p=0.075, repeat_penalty=1.1,
        context_tokens=settings.context_tokens, max_new_tokens=70, stop=list(fmt.stops),
    )
    first = person.name.split()[0]
    line = ""
    for _ in range(2):
        line = judge(f"{opener}{await get_client().generate(prompt, params)}".rstrip(), names, event=True,
                     scene_time=str((session.scene or {}).get("time") or ""))
        if line and re.search(rf"(?<!\w){re.escape(first)}(?!\w)", line, re.I):
            break
        line = ""
    position = max((m.position for m in session.members), default=-1) + 1
    session.members.append(SessionMember(character_id=person.id, position=position))
    message = Message(session_id=session.id, role=ROLE, content=line or f"{opener} {person.name} comes by.")
    db.add(message)
    db.commit()
    db.refresh(session)
    return message
