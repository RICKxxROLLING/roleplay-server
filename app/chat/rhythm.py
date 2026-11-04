"""A character's own day: when they're up, when they're tied up, how readily they write.

All optional, set per character. With none set, a character follows only the server-wide
rules in chat/autonomy.py. The owner's quiet hours always win: a night owl still doesn't
wake the owner at 3 AM.

Busy is soft. Someone at work can still slip away for a message, about one hour in five,
because "never during work" read as a timetable, and timetables are what this avoids.

`suggest` proposes a rhythm from the card. Prototyped on Stheno over all 17 test cards:
with example times in the prompt ("like 6:30 AM") nearly everyone woke between 5 and 7 and
went to bed around 10, and "often" came back almost every time. Without examples, and told
that some people keep late hours or work nights, Dina became a night owl (10 AM to 3 AM),
Joel "rarely" reaches out, and working hours differ by job. The answers then come in many
shapes ("Dawn, around 6:00 am", "by dusk"), so the server reads a time out of each, and
drops what it can't read or what makes no sense.
"""
from __future__ import annotations

import datetime as dt
import random
import re

from ..config import settings
from ..db.models import Character, ChatSession
from ..llm import GenerationParams, get_client
from ..prompt.formats import get_format
from . import group

#: How much the wait before writing stretches or shrinks.
REACH = {"often": 0.75, "sometimes": 1.0, "rarely": 1.75}
#: Chance that a given hour of work has room for a message.
SLIP_CHANCE = 0.2
#: Awake and busy spans outside these (in minutes) are a misread or a muddle.
AWAKE_SPAN = (480, 1320)
BUSY_SPAN = (60, 960)

PROMPT = """{name}: {about}

Personality: {personality}

Work out {name}'s usual day from what is written above: their work, habits and temperament. \
Some people are up before dawn, some keep late hours, some work nights. Answer in exactly these \
lines, giving clock times:

Reason: one short sentence about how {name} spends a day.
Wakes: when they usually get up.
Sleeps: when they usually go to bed.
Busy from: when their work or duties begin.
Busy until: when their work or duties end.
Reaches out: often, sometimes or rarely, by how readily {name} contacts people unprompted."""

_CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\b", re.I)
_CLOCK24 = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
#: In the order tried. Only words that mean one hour: "late" and "morning" don't.
_WORDS = [("midnight", "00:00"), ("mid-morning", "09:30"), ("noon", "12:00"), ("midday", "12:00"),
          ("dawn", "06:00"), ("sunrise", "06:00"), ("first light", "06:00"),
          ("dusk", "19:00"), ("sundown", "19:00"), ("sunset", "19:00")]


def read_time(text: str) -> str | None:
    """The first time of day in an answer, as HH:MM."""
    if m := _CLOCK.search(text):
        hour = int(m.group(1))
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if m.group(3).lower() == "p" else 0)
        return f"{hour:02d}:{int(m.group(2) or 0) % 60:02d}"
    if m := _CLOCK24.search(text):
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    low = text.lower()
    return next((t for word, t in _WORDS if word in low), None)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _span(start: str, end: str) -> int:
    return (_minutes(end) - _minutes(start)) % 1440


def _within(start: str, end: str, local: dt.datetime) -> bool:
    return (local.hour * 60 + local.minute - _minutes(start)) % 1440 < _span(start, end)


def parse(raw: str) -> dict:
    """What can be used from an answer: {wake, sleep, busy_from, busy_until, reach, reason}."""
    def line(label: str) -> str:
        m = re.search(rf"^[ \t*]*{label}[ \t*]*:(.*)$", raw, re.I | re.M)
        return m.group(1).strip() if m else ""

    out: dict[str, str] = {}
    wake, sleep = read_time(line("Wakes")), read_time(line("Sleeps"))
    if wake and sleep and AWAKE_SPAN[0] <= _span(wake, sleep) <= AWAKE_SPAN[1]:
        out.update(wake=wake, sleep=sleep)
    start, end = read_time(line("Busy from")), read_time(line("Busy until"))
    if start and end and BUSY_SPAN[0] <= _span(start, end) <= BUSY_SPAN[1]:
        out.update(busy_from=start, busy_until=end)
    reach = line("Reaches out").lower()
    word = next((w for w in ("often", "sometimes", "rarely") if w in reach), None)
    if word or "occasionally" in reach:
        out["reach"] = word or "sometimes"
    reason = raw.strip().split("\n")[0].strip()
    if reason and out:
        out["reason"] = reason[:200]
    return out


async def suggest(char: Character) -> dict:
    """A proposal from the card, for the owner to accept or change. Never saved here."""
    card = char.card or {}
    fmt = get_format(settings.prompt_format)
    body = PROMPT.format(name=char.name, about=(card.get("description") or "")[:900],
                         personality=(card.get("personality") or "")[:400])
    prompt = fmt.user.format(text=body) + fmt.joiner + fmt.response_open + "Reason:"
    params = GenerationParams(temperature=0.7, top_p=0.95, top_k=40, repeat_penalty=1.0, context_tokens=4096,
                              max_new_tokens=90, stop=list(fmt.stops))
    best: dict = {}
    for _ in range(2):
        got = parse(await get_client().generate(prompt, params))
        # Said of 19 people in 25 live, Joel's neighbours and hermits alike, so it says
        # little; the owner can still choose it. "Rarely" and "sometimes" are earned.
        if got.get("reach") == "often":
            del got["reach"]
        if len(got) > len(best):
            best = got
        if "wake" in best and "busy_from" in best:
            break
    return best


def asleep(char: Character, local: dt.datetime) -> bool:
    r = char.rhythm or {}
    return bool(r.get("wake") and r.get("sleep")) and not _within(r["wake"], r["sleep"], local)


def busy(char: Character, local: dt.datetime, session_id: int) -> bool:
    """Tied up right now, and this isn't one of the hours they can slip away."""
    r = char.rhythm or {}
    if not (r.get("busy_from") and r.get("busy_until")) or not _within(r["busy_from"], r["busy_until"], local):
        return False
    roll = random.Random(f"{session_id}:{char.id}:{local.date()}:{local.hour}").random()
    return roll >= SLIP_CHANCE


def free_cast(session: ChatSession, local: dt.datetime) -> list[Character]:
    """Who in the scene could write right now."""
    return [c for c in group.cast(session) if not asleep(c, local) and not busy(c, local, session.id)]


def clock_time(hhmm: str) -> str:
    h, m = hhmm.split(":")
    return dt.time(int(h), int(m)).strftime("%I:%M %p").lstrip("0")


def why_not(session: ChatSession, local: dt.datetime) -> str | None:
    """None when someone in the scene is free, else the reason for the chat's status line."""
    if free_cast(session, local):
        return None
    people = group.cast(session)
    if len(people) > 1:
        return "everyone here is asleep or busy"
    char = people[0]
    if asleep(char, local):
        return f"{char.name} is asleep until {clock_time(char.rhythm['wake'])}"
    return f"{char.name} is busy until {clock_time(char.rhythm['busy_until'])}"


def reach_factor(char: Character | None) -> float:
    return REACH.get(((char.rhythm or {}) if char else {}).get("reach", ""), 1.0)


def jitter(session: ChatSession) -> float:
    """A different wait after each message, steady between checks, so the gap isn't a clock."""
    last = session.messages[-1].id if session.messages else 0
    return random.Random(f"{session.id}:{last}").uniform(0.8, 1.4)


def day_line(char: Character, local: dt.datetime) -> str:
    """For the would-they-write question, only while they are tied up. Measured on Stheno,
    12 asks per hour, one chat: their whole schedule in the prompt cut the yeses at every
    hour, 9 to 4 even at a free 3 PM, since it read as a reason not to write."""
    r = char.rhythm or {}
    if r.get("busy_from") and r.get("busy_until") and _within(r["busy_from"], r["busy_until"], local):
        return f"{char.name} is usually tied up with work or duties until {clock_time(r['busy_until'])}."
    return ""
