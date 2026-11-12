"""Numbers about everything played here, for the Stats panel.

Worked out on each request from what is already stored: nothing extra is tracked, so a chat
that is deleted leaves the numbers with it. Single-user scale, so one pass over the messages
is fine; a few thousand messages take milliseconds.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from ..db import Character, ChatSession, Message, get_db
from ..db.models import CharacterThought, Encounter, PersonMemory, Standing, World

router = APIRouter(prefix="/stats", tags=["stats"])

#: How far back the day-by-day chart reaches.
DAYS = 60
TOP = 10
_WORD = re.compile(r"[A-Za-z']+")
# Words too common to say anything about how someone writes.
_STOP = frozenset("""a about above after again against all am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further had has have having
he her here hers herself him himself his how i if in into is it its itself just let me more most my myself no
nor not now of off on once only or other our ours out over own same she should so some such than that the their
theirs them themselves then there these they this those through to too under until up very was we were what when
where which while who whom why will with would you your yours yourself yourselves i'm it's don't that's i'll
you're can't there's i've we're oh yeah okay ok well just really got get like know think going one back""".split())
#: Habits of an 8B narrator, counted for fun across every character's replies.
TICS = {
    "smirks": r"\bsmirk\w*", "chuckles": r"\bchuckl\w*", "sighs": r"\bsigh\w*",
    "grins": r"\bgrin\w*", "eyes sparkle": r"\beyes?\b[^.]{0,20}\bsparkl\w*", "raises an eyebrow": r"\beyebrow\w*",
    "blushes": r"\bblush\w*", "pauses for a moment": r"\b(?:for a moment|a moment passes)\b",
    "a beat": r"\ba beat\b",
}


def _zone(tz: str):
    try:
        return ZoneInfo(tz) if tz else dt.timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return dt.timezone.utc


def _local(when: dt.datetime | None, zone) -> dt.datetime | None:
    if when is None:
        return None
    return (when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)).astimezone(zone)


def _words(text: str) -> list[str]:
    # Speech and narration alike; the asterisks are markup, not words.
    return _WORD.findall(text.replace("*", " "))


def _streaks(days: set[dt.date], today: dt.date | None = None) -> tuple[int, int]:
    """Longest run of consecutive days played, and the run ending today or yesterday. `today` is
    the viewer's date: the server's own is already tomorrow on a US evening."""
    if not days:
        return 0, 0
    ordered = sorted(days)
    best = run = 1
    for a, b in zip(ordered, ordered[1:]):
        run = run + 1 if (b - a).days == 1 else 1
        best = max(best, run)
    today = today or dt.date.today()
    current = 0
    day = today if today in days else today - dt.timedelta(days=1)
    while day in days:
        current += 1
        day -= dt.timedelta(days=1)
    return best, current


@router.get("")
def stats(tz: str = "", db: DbSession = Depends(get_db)) -> dict:
    zone = _zone(tz)
    people = {c.id: c for c in db.query(Character).filter(Character.host.is_(False)).all()}
    sessions = db.query(ChatSession).all()
    owner = {s.id: s for s in sessions}

    totals = Counter()
    per_day = Counter()
    per_hour = Counter()
    per_weekday = Counter()
    by_character = Counter()
    words_by_character = Counter()
    my_words = Counter()
    tics = Counter()
    days_played: set[dt.date] = set()
    longest = {"words": 0}
    most_swiped = {"swipes": 0}
    reply_lengths: list[int] = []

    for m in db.query(Message).all():
        s = owner.get(m.session_id)
        if s is None:
            continue
        words = _words(m.content)
        when = _local(m.created_at, zone)
        if m.role == "user":
            totals["your_messages"] += 1
            totals["your_words"] += len(words)
            my_words.update(w.lower() for w in words if w.lower() not in _STOP and len(w) > 2)
            if when:
                days_played.add(when.date())
                per_day[when.date()] += 1
                per_hour[when.hour] += 1
                per_weekday[when.weekday()] += 1
        elif m.role == "assistant":
            who = m.speaker_id or s.character_id
            if who not in people:
                continue
            totals["their_messages"] += 1
            totals["their_words"] += len(words)
            reply_lengths.append(len(words))
            by_character[who] += 1
            words_by_character[who] += len(words)
            for name, pattern in TICS.items():
                tics[name] += len(re.findall(pattern, m.content, re.I))
            if len(words) > longest["words"]:
                longest = {"words": len(words), "name": people[who].name, "chat": s.title,
                           "opening": " ".join(m.content.split())[:160]}
            swipes = len(m.alternatives or [])
            if swipes > most_swiped["swipes"]:
                most_swiped = {"swipes": swipes, "name": people[who].name, "chat": s.title}
            totals["swipes"] += max(0, swipes - 1)
        elif m.role == "narrator":
            totals["narrator_lines"] += 1

    today = dt.datetime.now(zone).date()
    best_streak, current_streak = _streaks(days_played, today)

    # Where each character stands with you: their best chat, since trust is per chat.
    standing: dict[int, dict] = {}
    for row in db.query(Standing).all():
        if row.character_id not in people or row.session_id not in owner:
            continue
        old = standing.get(row.character_id)
        score = row.trust + row.affection + row.respect
        if old is None or score > old["_score"]:
            standing[row.character_id] = {"name": people[row.character_id].name, "trust": round(row.trust),
                                          "affection": round(row.affection), "respect": round(row.respect),
                                          "_score": score}
    closest = sorted(standing.values(), key=lambda r: -r["_score"])[:TOP]
    for r in closest:
        r.pop("_score")

    # World chats: where you've been and who you've met.
    worlds = {w.id: w.name for w in db.query(World).all()}
    meetings_by_world = Counter()
    places = Counter()
    met: dict[int, set[int]] = defaultdict(set)
    for e in db.query(Encounter).all():
        s = owner.get(e.session_id)
        if s is None or s.world_id is None:
            continue
        meetings_by_world[worlds.get(s.world_id, "A deleted world")] += 1
        if e.place:
            places[e.place[0].upper() + e.place[1:]] += 1
        if e.character_id in people:
            met[s.world_id].add(e.character_id)

    def ranked(counter: Counter, label=lambda k: k) -> list[dict]:
        return [{"label": label(k), "value": v} for k, v in counter.most_common(TOP) if v]

    return {
        "tiles": {
            "chats": len(sessions),
            "world_chats": sum(1 for s in sessions if s.world_id is not None),
            "characters": sum(1 for c in people.values() if not c.extra),
            "extras": sum(1 for c in people.values() if c.extra),
            "worlds": len(worlds),
            "your_messages": totals["your_messages"],
            "your_words": totals["your_words"],
            "their_words": totals["their_words"],
            "average_reply_words": round(sum(reply_lengths) / len(reply_lengths)) if reply_lengths else 0,
            "days_played": len(days_played),
            "best_streak": best_streak,
            "current_streak": current_streak,
            "swipes": totals["swipes"],
            "narrator_lines": totals["narrator_lines"],
            "journal_entries": db.query(CharacterThought).count(),
            "people_remembering_you": db.query(PersonMemory).filter(PersonMemory.notes != "").count(),
            "people_met": sum(len(v) for v in met.values()),
        },
        "by_day": [{"label": (today - dt.timedelta(days=i)).isoformat(),
                    "value": per_day.get(today - dt.timedelta(days=i), 0)} for i in range(DAYS - 1, -1, -1)],
        "by_hour": [{"label": h, "value": per_hour.get(h, 0)} for h in range(24)],
        "by_weekday": [{"label": d, "value": per_weekday.get(i, 0)}
                       for i, d in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])],
        "top_characters": ranked(by_character, lambda k: people[k].name),
        "wordiest_characters": [{"label": people[k].name, "value": round(words_by_character[k] / n)}
                                for k, n in sorted(by_character.items(), key=lambda kv: -words_by_character[kv[0]] / kv[1])[:TOP]
                                if n >= 3],
        "your_words_most_used": ranked(my_words),
        "tics": ranked(tics),
        "standing": closest,
        "meetings_by_world": ranked(meetings_by_world),
        "places": ranked(places),
        "longest_reply": longest if longest["words"] else None,
        "most_swiped": most_swiped if most_swiped["swipes"] > 1 else None,
    }
