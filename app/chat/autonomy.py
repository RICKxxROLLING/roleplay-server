"""Characters writing first.

A chat with `autonomous` on may get a message nobody asked for. The scheduler
(`run_forever`) checks such chats every few minutes; each check is three
filters, cheapest first:

1. **Rules, no model** (`eligible`): quiet hours, a minimum gap that doubles with
   every unanswered message, a daily cap, nothing already generating, and the
   chat not suspended -- three unanswered messages and it waits for the user.
2. **Would they?** (`decide`): a short generation asks whether this character,
   at this hour, after how the last exchange ended, would reach out -- and why.
   The reason is kept; it is what the message is about.
3. **The message**: the normal reply path (`stream_reply`) with an `initiative`
   line in the tail, so the guard, redos, memory and scene all apply. Saved as
   `initiated`, then pushed as an empty ping (app/push.py).

Everything shares the one GPU, so a check is skipped while any reply is being
generated, and messages go out one at a time.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import random
import re

from sqlalchemy.orm import Session as DbSession

from ..config import settings
from ..db.models import ChatSession, Message
from ..llm import GenerationParams, get_client
from ..prompt.formats import get_format

log = logging.getLogger(__name__)

#: Replies currently generating, from any source. The scheduler waits for zero.
ACTIVE = 0


def _utc(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def local_now(now: dt.datetime | None = None) -> dt.datetime:
    """Now in the owner's zone (settings.timezone, set from their browser)."""
    now = _utc(now or dt.datetime.now(dt.timezone.utc))
    if settings.timezone:
        try:
            from zoneinfo import ZoneInfo

            return now.astimezone(ZoneInfo(settings.timezone))
        except Exception:
            pass
    return now.astimezone()


def _minutes(hhmm: str) -> int:
    h, _, m = (hhmm or "0:0").partition(":")
    return int(h) % 24 * 60 + int(m or 0) % 60


def in_quiet_hours(local: dt.datetime) -> bool:
    start, end = _minutes(settings.autonomy_quiet_start), _minutes(settings.autonomy_quiet_end)
    t = local.hour * 60 + local.minute
    if start == end:
        return False
    return start <= t < end if start < end else (t >= start or t < end)


def unanswered(session: ChatSession) -> int:
    """Messages the character started since the user last wrote."""
    n = 0
    for m in reversed(session.messages):
        if m.role == "user":
            break
        if m.initiated:
            n += 1
    return n


def suspended(session: ChatSession) -> bool:
    return unanswered(session) >= settings.autonomy_max_unanswered


def sent_today(session: ChatSession, local: dt.datetime) -> int:
    day = local.date()
    return sum(1 for m in session.messages
               if m.initiated and local_now(m.created_at).date() == day)


def gap_needed(session: ChatSession) -> dt.timedelta:
    """The minimum quiet before writing, doubling per unanswered message: a
    character who has written twice without reply waits four times as long."""
    return dt.timedelta(minutes=settings.autonomy_min_gap_minutes * 2 ** unanswered(session))


def eligible(session: ChatSession, now: dt.datetime | None = None) -> str | None:
    """None when a check may go ahead, else the reason it may not."""
    now = _utc(now or dt.datetime.now(dt.timezone.utc))
    local = local_now(now)
    if not session.autonomous:
        return "off for this chat"
    if not session.messages:
        return "nothing said yet"
    if suspended(session):
        return f"waiting for a reply after {unanswered(session)} messages"
    if in_quiet_hours(local):
        return "quiet hours"
    if sent_today(session, local) >= settings.autonomy_max_per_day:
        return "daily limit reached"
    since = now - _utc(session.messages[-1].created_at)
    if since < gap_needed(session):
        return "too soon since the last message"
    if session.next_check_at and now < _utc(session.next_check_at):
        return "checked recently"
    if ACTIVE:
        return "a reply is generating"
    return None


def humanise(delta: dt.timedelta) -> str:
    mins = int(delta.total_seconds() // 60)
    if mins < 90:
        return f"{max(mins, 1)} minutes"
    hours = mins / 60
    if hours < 36:
        return f"about {round(hours)} hours"
    return f"about {round(hours / 24)} days"


def clock(local: dt.datetime) -> str:
    """'Tuesday, 3:40 PM' -- what the model reads as now."""
    return local.strftime("%A, %I:%M %p").replace(" 0", " ")


def time_line(session: ChatSession, now: dt.datetime | None = None) -> str:
    """For any reply after a long silence: the hour, and how long it has been.
    Under an hour it says nothing -- mid-conversation, time isn't news."""
    msgs = [m for m in session.messages if m.role in ("user", "assistant")]
    if len(msgs) < 2:
        return ""
    now = _utc(now or dt.datetime.now(dt.timezone.utc))
    since = now - _utc(msgs[-2].created_at)
    if since < dt.timedelta(hours=1):
        return ""
    return f"It is now {clock(local_now(now))}; {humanise(since)} have passed since the previous message."


# --- would they write? ----------------------------------------------------------------

DECIDE_PROMPT = """{char} and {user} are in an ongoing story. It is now {now}, and \
{gap} have passed since their last exchange, which ended like this:

{tail}
{scene}
{char}'s personality: {personality}

Would {char}, being who they are, reach out to {user} right now without being asked -- \
send a message, or come find them? Think about unfinished business, a promise or plan, \
their feelings about {user}, and what {char} would be doing at this hour. People don't \
write every time they could.

Answer YES or NO, then one short sentence: what {char} would reach out about, or why not."""

_ANSWER = re.compile(r"\b(YES|NO)\b[\s.,:;-]*(.*)", re.I | re.S)


def _excerpt(session: ChatSession, names: dict[int, str], user: str, n: int = 4) -> str:
    lines = []
    for m in [m for m in session.messages][-n:]:
        who = user if m.role == "user" else names.get(m.speaker_id or session.character_id, "Someone")
        text = re.sub(r"\s+", " ", m.content).strip()
        lines.append(f"{who}: {text[-500:]}")
    return "\n".join(lines)


def build_decide_prompt(session: ChatSession, char, user: str, now: dt.datetime) -> str:
    from ..cards.models import CharacterCard
    from ..memory.scene import render as render_scene
    from . import group

    card = CharacterCard.model_validate(char.card or {})
    names = {c.id: c.name for c in group.cast(session)}
    body = DECIDE_PROMPT.format(
        char=char.name, user=user, now=clock(local_now(now)),
        gap=humanise(_utc(now) - _utc(session.messages[-1].created_at)),
        tail=_excerpt(session, names, user),
        scene=f"\nWhere things stood:\n{render_scene(session.scene)}\n" if session.scene else "",
        personality=(card.personality or card.description or "")[:600].strip(),
    )
    fmt = get_format(settings.prompt_format)
    return fmt.user.format(text=body) + fmt.joiner + fmt.response_open + "Answer:"


def parse_decision(raw: str) -> tuple[bool, str]:
    m = _ANSWER.search(raw or "")
    if not m:
        return False, ""
    reason = m.group(2).strip().split("\n")[0].strip(" -")
    return m.group(1).upper() == "YES", reason[:200]


async def decide(session: ChatSession, char, user: str, now: dt.datetime | None = None) -> tuple[bool, str]:
    params = GenerationParams(
        temperature=0.8, top_p=0.95, top_k=40, repeat_penalty=1.0,
        context_tokens=settings.context_tokens, max_new_tokens=60,
        stop=list(get_format(settings.prompt_format).stops),
    )
    raw = await get_client().generate(build_decide_prompt(session, char, user, now or dt.datetime.now(dt.timezone.utc)), params)
    return parse_decision(raw)


def initiative_line(char: str, user: str, reason: str, session: ChatSession,
                    now: dt.datetime | None = None) -> str:
    """The tail line that turns a reply into a message nobody asked for."""
    now = _utc(now or dt.datetime.now(dt.timezone.utc))
    since = humanise(now - _utc(session.messages[-1].created_at))
    about = f" {reason.rstrip('.')}." if reason else ""
    # Measured on Stheno against three live chats: "a message like one sent from a
    # phone" gave letters ("Hey Riley, ... Best, Dina") and long scene-setting that
    # ran into the token cap; and the Jackson cards have no phones -- Garrick wrote
    # a note on parchment. So: brief, the world's own medium, straight to the point.
    return (f"It is now {clock(local_now(now))}; {since} have passed since the last message, and "
            f"{user} hasn't written. {char} reaches out to {user} first, of their own accord:{about} "
            f"Keep it brief -- a few lines. If they are apart, write just the message itself, sent "
            f"the way it would be in {char}'s world (a text, a note, a radio call), in {char}'s own "
            f"voice and straight to the point. If they are together, {char} simply starts talking.")


# --- writing ---------------------------------------------------------------------------


def _preview(text: str) -> str:
    """Notification text: the words, not the stage directions."""
    quotes = [a or b for a, b in re.findall(r'"([^"]+)"|“([^”]+)”', text)]
    plain = " ".join(quotes) if quotes else re.sub(r"\*[^*]*\*", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip() or re.sub(r"\s+", " ", text.replace("*", "")).strip()
    return plain[:140] + ("…" if len(plain) > 140 else "")


async def write_first(db: DbSession, session: ChatSession, reason: str = "",
                      now: dt.datetime | None = None) -> Message | None:
    """Generate, save and announce one message the character starts. None if
    nothing usable came back (the guard can empty a reply)."""
    from .. import push
    from . import group
    from .orchestrator import stream_reply

    speaker = group.choose_speaker(session, "")
    user = session.persona.name if session.persona else "You"
    before = session.messages[-1].id if session.messages else 0
    line = initiative_line(speaker.name, user, reason, session, now)
    async for _event in stream_reply(db, session, {"max_new_tokens": 300}, speaker=speaker,
                                     initiative=line):
        pass
    db.refresh(session)
    last = session.messages[-1] if session.messages else None
    if last is None or last.id == before or last.role != "assistant":
        return None
    push.notify(db, speaker.name, _preview(last.content), session_id=session.id)
    await push.ping_all(db)
    return last


async def check(db: DbSession, session: ChatSession, now: dt.datetime | None = None,
                force: bool = False) -> dict:
    """One scheduler check of one chat. `force` skips the rules and the
    decision (the "Message me now" button)."""
    from . import group

    now = _utc(now or dt.datetime.now(dt.timezone.utc))
    if not force:
        why_not = eligible(session, now)
        if why_not:
            return {"sent": False, "why": why_not}
    speaker = group.choose_speaker(session, "")
    user = session.persona.name if session.persona else "You"
    reason = ""
    if not force:
        yes, reason = await decide(session, speaker, user, now)
        if not yes:
            # Irregular re-checks, so "no" doesn't become a timetable.
            wait = settings.autonomy_min_gap_minutes * random.uniform(0.3, 0.8)
            session.next_check_at = now + dt.timedelta(minutes=wait)
            db.commit()
            return {"sent": False, "why": f"decided not to: {reason}" if reason else "decided not to"}
    msg = await write_first(db, session, reason, now)
    session.next_check_at = None
    db.commit()
    if msg is None:
        return {"sent": False, "why": "nothing usable was written"}
    return {"sent": True, "why": reason, "message_id": msg.id}


async def tick(db: DbSession) -> list[dict]:
    """Check every chat that has writing-first on, one message at a time."""
    out = []
    for session in db.query(ChatSession).filter(ChatSession.autonomous.is_(True)).all():
        try:
            result = await check(db, session)
        except Exception as exc:  # never let one chat stop the loop
            log.warning("autonomy check failed for session %s: %s", session.id, exc)
            result = {"sent": False, "why": f"error: {exc}"}
        out.append({"session_id": session.id, **result})
    return out


async def run_forever() -> None:
    """The scheduler. Started from the app's lifespan; cancelled at shutdown."""
    from ..db import SessionLocal

    # A little delay at boot so a restart doesn't fire into a cold model.
    await asyncio.sleep(60)
    while True:
        try:
            with SessionLocal() as db:
                await tick(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("autonomy tick failed: %s", exc)
        await asyncio.sleep(max(1, settings.autonomy_check_minutes) * 60)
