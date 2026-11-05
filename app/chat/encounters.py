"""World chats: a chat based in a world rather than on one character.

The chat is a series of encounters. You arrive somewhere, a person there speaks first, you
talk, and you leave with one goodbye or with Walk on. Then someone else, somewhere else. Only
the user ends an encounter, and what is said to one person stays with them.

Three things make the privacy hold, each from the prototype on Stheno (two hand-written locals,
one store chat, so read the numbers as direction):
  - Walk on moves the summary's watermark past the encounter and empties the summary and facts.
    The next prompt's history, summary and facts then hold this encounter alone. With the last
    conversation still in the prompt, a stranger brought it up in 3 of 8 openings; out of it, 0 of 16.
  - What a person keeps is a short note of their own (`PersonMemory`), written as you part and read
    back as their known facts. Returning to someone without one, 0 of 8 remembered the deal; with
    one, 6 of 8.
  - Recall and pins only reach encounters the speaker was in (`private_ids`).

The chat's `character` is one hidden host character that never speaks, because
`ChatSession.character_id` is NOT NULL and migrations are additive. It belongs to no world, so
no roster or visitor list can pick it up, and it owns every world chat, so deleting a person
can't take a chat with them through the `Character.sessions` cascade.
"""
from __future__ import annotations

import random
import re

from ..cards.models import CharacterCard
from ..config import settings
from ..db.models import Character, ChatSession, Encounter, Message, PersonMemory
from ..llm import GenerationParams, get_client
from ..memory.scene import blank
from ..memory.summarizer import merge_facts, parse_facts
from ..prompt.formats import get_format
from . import group, narrator

HOST_NAME = "(world chat)"
#: Most people's notes stay well under this; each line costs context on every turn with them.
MEMORY_CAP = 12
#: Of the encounter, for the parting note. A long talk's earlier part is in its summary.
NOTE_TRANSCRIPT_CHARS = 6000

# --- Tail lines -------------------------------------------------------------------------
# Acting like acquaintances: 5 of 8 openings without the first, 1 of 8 with it.
NEVER_MET = ("{{char}} and {{user}} have never spoken before; to {{char}}, {{user}} is someone "
             "seen around town at most.")
NEVER_MET_LATER = ("{{char}} and {{user}} had never spoken before today; to {{char}}, {{user}} "
                   "was someone seen around town at most.")
FIRST_WORD = "{{char}} notices {{user}} and speaks first"
AGAIN = "{{char}} and {{user}} have met before. {{char}} sees {{user}} again and speaks first."
# Without a greeting to copy, some openings dropped the quote marks around speech.
FORMAT_LINE = 'Spoken words go in "double quotes" and actions in *asterisks*.'
# A spoken goodbye: 4 of 6 with this, 0 of 6 without. Nobody followed in either, 12 of 12.
GOODBYE = "{{user}} is leaving. {{char}} stays where {{char}} is and says one brief goodbye."
# Addressed the user 14 of 16 with a reason from the server, 9 of 16 without. The prototype's
# reasons named things in the scene; these have to suit anyone, anywhere.
REASONS = [
    "asking {{user}} for a small favour",
    "asking {{user}} to lend a hand for a moment",
    "with a remark about the weather",
    "with a complaint about the day so far",
    "asking whether {{user}} has a moment",
    "offering {{user}} something they have spare",
    "asking {{user}} what they make of something nearby",
    "asking {{user}} for directions",
]
TIMES = ["morning", "midday", "afternoon", "evening", "night"]

# --- Passes -----------------------------------------------------------------------------
# A plain "keep names, objects, numbers" lost the reason behind the deal. Asking for the why
# first, over two scenes, 8 tries each: 8 and 8 of 8 kept it, against 5 and 8 of 8.
PARTING = """Below is a meeting between {user} and {char}. Write what {char} will remember about it afterwards.
{earlier}
{transcript}

Write 3 to 5 short lines, each starting with "- ". The first line says why {user} came and who it was for. \
The others say what was given, what was promised and when. Plain statements, third person, using both names, \
only what was said above."""
WHERE = """{setting}

About {name}: {about}

It is {time}. Where would {name} be right now? Answer with the place only, in a few words."""
ARRIVAL = """{setting}

About {name}: {about}

Write one or two short sentences setting the scene at {place}, {time}. {name} is there, busy with something \
that suits them. It is quiet and ordinary, and nobody is in danger. Say nothing about anyone else arriving."""

_PREPOSITION = re.compile(r"^(at|in|on|by|near|outside|inside|under|behind|beside|along|across|up|down)\b", re.I)
_NAME_WORD = re.compile(r"\b[A-Z][a-z]{2,}\b")
_LEAVE_ACT = re.compile(
    r"\b(?:leave|leaves|walk out|walks out|walk away|walks away|head out|heads out|head for the door|"
    r"heads for the door|move on|moves on|wander on|wanders on|go home|goes home|head home|heads home|"
    r"head over to|heads over to|turn and go|turn and leave|turns and leaves|step out|steps out|"
    r"carry on down|carries on down|set off|sets off|take my leave|takes? (?:his|her|their) leave)\b", re.I)
_LEAVE_SAID = re.compile(
    r"\b(?:good\s?bye|bye|farewell|i'?m off|i should (?:go|get going|head off)|i(?:'d| had) better (?:go|get going)|"
    r"i'?ll let you get back to it|see you (?:around|later|tomorrow|soon)|take care)\b(?=\s*[,.!…\"”]|\s*$)", re.I)
_QUOTED = re.compile(r'"[^"]*"|“[^”]*”')
#: Going to another part of the same room, or staying, isn't leaving.
_LEAVE_NOT = re.compile(r"\b(?:never|won'?t|wouldn'?t|couldn'?t|can'?t|don'?t|didn'?t|not)\s+(?:\w+\s+)?(?:leave|go)\b", re.I)


class NobodyHere(ValueError):
    """The world has nobody to meet, in words for the owner."""


def host(db) -> Character:
    """The one hidden character that owns every world chat."""
    row = db.query(Character).filter(Character.host.is_(True)).order_by(Character.id).first()
    if row is None:
        row = Character(name=HOST_NAME, card=CharacterCard(name=HOST_NAME).model_dump(), host=True)
        db.add(row)
        db.flush()
    return row


def _user(session: ChatSession) -> str:
    return session.persona.name if session.persona else "You"


def messages_of(session: ChatSession, encounter: Encounter | None) -> list[Message]:
    """An encounter's messages, by range. Ranges rather than a column on Message, so that no
    place that writes a message has to know about encounters."""
    if encounter is None:
        return []
    later = [e.start_id for e in session.encounters if e.start_id > encounter.start_id]
    end = min(later) if later else None
    return [m for m in session.messages if m.id >= encounter.start_id and (end is None or m.id < end)]


def private_ids(session: ChatSession, speaker: Character) -> set[int]:
    """Messages from encounters `speaker` wasn't in."""
    out: set[int] = set()
    for e in session.encounters:
        if e.character_id != speaker.id:
            out |= {m.id for m in messages_of(session, e)}
    return out


def last_user_text(session: ChatSession) -> str:
    """The user's latest line to the person in front of them, never to the one before."""
    here = messages_of(session, group.current_encounter(session))
    return next((m.content for m in reversed(here) if m.role == "user"), "")


def is_leaving(text: str) -> bool:
    """Whether the user's line ends the conversation. A question isn't, and neither is talk of
    someone else's goodbye. Tried against asking the model yes or no: 22 of 24 test lines right
    against 18, and the rule costs nothing. Those lines were written with the rule, so expect worse."""
    words = text.strip()
    if not words:
        return False
    narration = _QUOTED.sub(" ", words)
    if _LEAVE_ACT.search(narration) and not _LEAVE_NOT.search(narration):
        return True
    return any(not q.rstrip('"” ').endswith("?") and _LEAVE_SAID.search(q.strip('"“” '))
               for q in _QUOTED.findall(words))


def leaving(session: ChatSession) -> bool:
    return is_leaving(last_user_text(session))


def is_opening(session: ChatSession, exclude_ids: set[int] | None = None) -> bool:
    """Nobody has said anything in this encounter yet."""
    here = messages_of(session, group.current_encounter(session))
    return not any(m.role in ("user", "assistant") and m.id not in (exclude_ids or ()) for m in here)


def _knows_user(card: CharacterCard, user: str) -> bool:
    """A card written around the user knows them already, whatever this chat has seen."""
    text = " ".join((card.description, card.personality))
    return "{{user}}" in text or "<USER>" in text or (user != "You" and bool(
        re.search(rf"(?<!\w){re.escape(user)}(?!\w)", text)))


def met_before(session: ChatSession, encounter: Encounter) -> bool:
    return any(e.character_id == encounter.character_id and e.id != encounter.id and e.id < encounter.id
               for e in session.encounters)


def tail_note(session: ChatSession, card: CharacterCard, exclude_ids: set[int] | None = None) -> str:
    """The encounter's line for the prompt's tail."""
    encounter = group.current_encounter(session)
    if encounter is None:
        return ""
    known = met_before(session, encounter) or _knows_user(card, _user(session))
    if is_opening(session, exclude_ids):
        if met_before(session, encounter):
            return f"{AGAIN} {FORMAT_LINE}"
        reason = f", {encounter.reason}" if encounter.reason else ""
        first = f"{FIRST_WORD}{reason}."
        return " ".join(p for p in ("" if known else NEVER_MET, first, FORMAT_LINE) if p)
    parts = ["" if known else NEVER_MET_LATER, GOODBYE if leaving(session) else ""]
    return " ".join(p for p in parts if p)


def persona_for(session: ChatSession, speaker: Character) -> str:
    """The persona without sentences naming anyone else in this world. Strangers named Dina in
    6 of 16 openings because the persona mentioned her; she keeps hearing it, they don't."""
    text = session.persona.description if session.persona else ""
    world = group.world_of(session)
    others = [c.name for c in (world.characters if world else []) if c.id != speaker.id]
    names = {n for full in others for n in (full, full.split()[0]) if n}
    if not text or not names:
        return text
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")(?!\w)")
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(s for s in sentences if not pattern.search(s))


def remembered(session: ChatSession, speaker: Character, db) -> str:
    row = db.get(PersonMemory, (session.id, speaker.id)) if db is not None else None
    return row.notes if row else ""


def _setting(session: ChatSession) -> str:
    world = group.world_of(session)
    return f"Setting: {world.description[:900]}" if world and world.description else ""


def _about(person: Character) -> str:
    return " ".join(((person.card or {}).get("description") or "").split())[:400]


def _params(max_new_tokens: int, temperature: float = 0.9) -> GenerationParams:
    return GenerationParams(temperature=temperature, top_p=1, top_k=50, min_p=0.075, repeat_penalty=1.1,
                            context_tokens=settings.context_tokens, max_new_tokens=max_new_tokens,
                            stop=list(get_format(settings.prompt_format).stops))


def _ask(body: str, prefill: str = "") -> str:
    fmt = get_format(settings.prompt_format)
    return fmt.user.format(text=body) + fmt.joiner + fmt.response_open + prefill


def choose_person(session: ChatSession, place: str = "", wanted: int | None = None) -> Character:
    """Who comes next: the one asked for, else whoever's card mentions the place, else whoever has
    been met least. Never the one just left unless there's nobody else."""
    world = group.world_of(session)
    people = list(world.characters) if world else []
    if wanted is not None:
        chosen = next((c for c in people if c.id == wanted), None)
        if chosen is None:
            raise NobodyHere("That person isn't in this world.")
        return chosen
    if not people:
        raise NobodyHere("This world has nobody in it yet. Add someone to it under Worlds.")
    last = session.encounters[-1].character_id if session.encounters else None
    pool = [c for c in people if c.id != last] or people
    if place:
        wanted_words = {w for w in re.findall(r"[a-z]{4,}", place.lower())}
        fits = [c for c in pool if wanted_words & set(re.findall(r"[a-z]{4,}", _about(c).lower()))]
        if fits:
            pool = fits
    met = {c.id: sum(e.character_id == c.id for e in session.encounters) for c in pool}
    fewest = min(met.values())
    return random.choice([c for c in pool if met[c.id] == fewest])


def clean_place(raw: str) -> str:
    """A few words naming a place, or "" to refuse them. The model tends to run on."""
    place = re.split(r"[\n.,;:!?(]| while | where | as ", raw.strip(), maxsplit=1)[0].strip(' "*“”')
    if not place or len(place.split()) > 8 or len(place) > 60:
        return ""
    return place


async def choose_place(session: ChatSession, person: Character, time: str) -> str:
    prompt = _ask(WHERE.format(setting=_setting(session), name=person.name, about=_about(person), time=time), "At ")
    place = clean_place(await get_client().generate(prompt, _params(16, 0.7)))
    return place if place and not re.search(rf"(?<!\w){re.escape(person.name.split()[0])}(?!\w)", place) else "the main street"


def _opener(place: str) -> str:
    return f"{place[0].upper()}{place[1:]}," if _PREPOSITION.match(place) else f"At {place},"


async def arrival_line(session: ChatSession, person: Character, place: str, time: str) -> str:
    """Always something, as the chat can't go on without it. Names only the user are refused,
    since a line with them in it writes what they're doing."""
    user = _user(session)
    names = [user, user.split()[0]] if user != "You" else []
    opener = _opener(place)
    prompt = _ask(ARRIVAL.format(setting=_setting(session), name=person.name, about=_about(person),
                                 place=place, time=time), opener)
    for _ in range(narrator.ATTEMPTS - 1):
        line = narrator.judge(f"{opener}{await get_client().generate(prompt, _params(70))}".rstrip(), names,
                              event=True, scene_time=time)
        if line:
            return line
    return f"{opener} {person.name} is there, this {time}."


def _supported(line: str, transcript: str) -> bool:
    """Names the note uses must have come up. It can't catch invented common words: the model
    called Dina his sister in all 8 notes of one scene, which is why notes can be edited."""
    return all(w in transcript for w in _NAME_WORD.findall(line) if w not in ("The", "They", "She", "His", "Her", "Their"))


async def write_note(session: ChatSession, encounter: Encounter, db) -> None:
    """What this person keeps of the meeting. Nothing usable costs the new note, not the old one."""
    person = encounter.character
    if person is None:
        return
    lines = [m for m in messages_of(session, encounter)
             if m.id > (session.summarized_upto_id or 0) and m.role in ("user", "assistant")]
    if not any(m.role == "user" for m in messages_of(session, encounter)):
        return
    user = _user(session)
    transcript = "\n".join(f"{user if m.role == 'user' else person.name}: {m.content}" for m in lines)[-NOTE_TRANSCRIPT_CHARS:]
    earlier = f"\nEarlier in the meeting: {session.summary}\n" if session.summary else ""
    prompt = _ask(PARTING.format(user=user, char=person.name, earlier=earlier, transcript=transcript), "- ")
    params = GenerationParams(temperature=0.3, top_p=0.9, top_k=40, repeat_penalty=1.0,
                              context_tokens=settings.context_tokens, max_new_tokens=200,
                              stop=list(get_format(settings.prompt_format).stops))
    raw = await get_client().generate(prompt, params)
    source = f"{transcript} {session.summary or ''} {person.name} {user}"
    new = [f for f in parse_facts(raw, user) if _supported(f, source)]
    if not new:
        return
    row = db.get(PersonMemory, (session.id, person.id))
    old = [line[2:].strip() if line.startswith("- ") else line.strip()
           for line in (row.notes if row else "").splitlines() if line.strip()]
    merged = "\n".join(f"- {f}" for f in merge_facts(old, new, cap=MEMORY_CAP, leads={person.name.split()[0], user.split()[0]}))
    if row:
        row.notes = merged
    else:
        db.add(PersonMemory(session_id=session.id, character_id=person.id, notes=merged))


def next_time(session: ChatSession) -> str:
    """The day moves on one step every second encounter. Hand edits to the scene are kept
    until then, and an unfamiliar time simply starts again from there."""
    now = str((session.scene or {}).get("time") or "").lower()
    count = len(session.encounters)
    current = next((t for t in TIMES if t in now), None)
    if current is None:
        return now if now and now != "unknown" else TIMES[0]
    if count % 2:
        return now
    return TIMES[(TIMES.index(current) + 1) % len(TIMES)]


async def walk_on(db, session: ChatSession, place: str = "", wanted: int | None = None) -> Encounter:
    """End this encounter and start the next. Every model call comes before any write, so if the
    backend is down the user is still standing where they were."""
    place = " ".join(place.split())[:60]
    person = choose_person(session, place, wanted)
    current = group.current_encounter(session)
    time = next_time(session) if session.encounters else TIMES[0]
    place = place or await choose_place(session, person, time)
    line = await arrival_line(session, person, place, time)
    if current is not None:
        await write_note(session, current, db)
        current.ended = True
    last_id = session.messages[-1].id if session.messages else 0
    message = Message(session_id=session.id, role=narrator.ROLE, content=line)
    db.add(message)
    db.flush()
    # The whole of the fold's state starts afresh: it held the last person's conversation.
    session.summarized_upto_id = last_id
    session.summary = ""
    session.facts = ""
    scene = blank(person.name, _user(session))
    scene.update(location=place, time=time)
    session.scene = scene
    session.scene_prev = dict(scene)
    encounter = Encounter(session_id=session.id, character_id=person.id, place=place, time=time,
                          reason=random.choice(REASONS), start_id=message.id)
    db.add(encounter)
    db.commit()
    db.refresh(session)
    return encounter
