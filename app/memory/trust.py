"""Earned trust: how a character stands with the user, in three numbers.

Trust, affection and respect, 0-100, per character per chat. After each reply a
short pass asks the model what the user's latest action changed; the server then
decides how much of that counts. The model proposes, the server disposes: left to
itself an 8B warms up on every turn (four kind lines took a character to
"indispensable comrade" in the thoughts pass), so gains are capped and shrink as a
score rises, while losses land in full. Slow to earn, quick to lose.
"""
from __future__ import annotations

import re

from ..config import settings
from ..llm import GenerationParams, get_client
from ..prompt.formats import get_format

FIELDS = ("trust", "affection", "respect")
#: Where a relationship starts when the card gives no reason to start elsewhere.
START = {"trust": 30.0, "affection": 30.0, "respect": 30.0}
#: The most one exchange can move a score, whatever the model proposes.
MAX_GAIN = 2
MAX_LOSS = 5

# Prototyped on Stheno. Shown the character's reply too, the pass credited the user with
# whatever the reply invented ("Mm." scored +2 for "kept promise to wake Dina") and
# silent turns earned as much as real help. Asked for numbers first, it committed before
# reasoning ("You're useless on patrol": +2 trust, reason "insult"). So: only the user's
# line, with what it answered as context, and the reason comes first.
TRUST_PROMPT = """You are scoring how one thing {user} did changed {char}'s feelings about {user}.

{char} had just said or done this:
{before}

Then {user} did this:
{action}

Judge only {user}'s line above, nothing else. Most lines change nothing.

0: small talk, silence, short replies, ordinary questions, making a promise.
+1: a small kindness or a good joke. +2: real help, a promise actually kept, or a hard truth told kindly.
-1: rudeness or carelessness. -3: a lie, a broken promise, cruelty, theft, betrayal, or shrugging one off.

Examples of how to score Trust:
"I promise I'll be there." 0, a promise isn't kept yet.
"I was there, like I said I'd be." +2, a promise kept.
"I lied to you. So what?" -3, admitting it without remorse is not honesty.
"I'm sorry I lied. Here's the truth." +2, a real apology.
"You'd do the same to me." -1, an excuse.
"Mm." 0.

Trust is whether {char} can rely on {user}. Affection is how much {char} likes {user}. \
Respect is how highly {char} rates {user}'s ability and character.

Reply with exactly these lines:
Reason: what {user} did, in under ten words
Trust: a number
Affection: a number
Respect: a number"""

_LINE = re.compile(r"^\s*\**(Trust|Affection|Respect|Reason)\**\s*:\s*(.+)$", re.I | re.M)
_NUMBER = re.compile(r"^[+-]?\d+")


def parse_change(raw: str) -> dict | None:
    """{"trust": int, "affection": int, "respect": int, "reason": str}, or None
    unless all three numbers are there -- a half-answer moves nothing."""
    found: dict = {}
    for name, value in _LINE.findall("Reason:" + (raw or "")):
        name = name.lower()
        if name in found:
            continue
        if name == "reason":
            found[name] = value.strip()[:160]
            continue
        m = _NUMBER.match(value.strip())
        if not m:
            return None
        found[name] = int(m.group())
    if not all(f in found for f in FIELDS):
        return None
    found.setdefault("reason", "")
    return found


def apply_change(scores: dict, change: dict) -> dict:
    """The server's half: cap each proposal, shrink gains as a score rises."""
    out = dict(scores)
    for f in FIELDS:
        delta = max(-MAX_LOSS, min(MAX_GAIN, change.get(f, 0)))
        value = float(out.get(f, START[f]))
        # At 30 a +2 counts as 1.4; at 80, as 0.4. The top has to be earned.
        # A +1 counts half: quiet lines still draw the odd +1 from the model, and
        # small talk must not add up to trust the way real help does.
        step = (delta if delta > 1 else delta / 2) * (100 - value) / 100 if delta > 0 else delta
        out[f] = round(max(0.0, min(100.0, value + step)), 2)
    return out


def build_trust_prompt(user_text: str, before: str, char: str, user: str) -> str:
    fmt = get_format(settings.prompt_format)
    body = TRUST_PROMPT.format(char=char, user=user, before=before.strip()[-500:] or "(nothing yet)",
                               action=user_text.strip()[-800:])
    return fmt.user.format(text=body) + fmt.joiner + fmt.response_open + "Reason:"


async def score(user_text: str, before: str, char: str, user: str) -> dict | None:
    params = GenerationParams(
        temperature=0.3, top_p=0.9, top_k=40, repeat_penalty=1.0,
        context_tokens=settings.context_tokens, max_new_tokens=70,
        stop=list(get_format(settings.prompt_format).stops),
    )
    raw = await get_client().generate(build_trust_prompt(user_text, before, char, user), params)
    return parse_change(raw)


# --- the starting point ------------------------------------------------------------

SEED_PROMPT = """You are scoring how {char} feels about {user} right now, from 0 to 100.

About {char}:
{card}
{story}
0 is hatred, 30 is a stranger or a new acquaintance, 50 is a friend, 70 is a close friend \
of many years. Nobody starts above 70.

Trust is whether {char} can rely on {user}. Affection is how much {char} likes {user}. \
Respect is how highly {char} rates {user}'s ability and character.

Reply with exactly these lines:
Reason: how they know each other, in under ten words
Trust: a number
Affection: a number
Respect: a number"""

#: A first impression can't begin at the top: the rest has to be earned in the chat.
SEED_MAX = 70


async def seed(card, summary: str, user: str) -> dict:
    """Starting scores from the card and any story so far, so a childhood friend and
    a stranger don't begin alike. START if the model misfires."""
    from ..prompt import substitute

    char = card.name or "Character"
    text = substitute(" ".join(p for p in (card.description, card.personality, card.scenario) if p), char, user)
    body = SEED_PROMPT.format(char=char, user=user, card=text[:1500],
                              story=f"\nThe story so far:\n{summary[-1200:]}\n" if summary else "")
    fmt = get_format(settings.prompt_format)
    params = GenerationParams(
        temperature=0.3, top_p=0.9, top_k=40, repeat_penalty=1.0,
        context_tokens=settings.context_tokens, max_new_tokens=70,
        stop=list(fmt.stops),
    )
    raw = await get_client().generate(
        fmt.user.format(text=body) + fmt.joiner + fmt.response_open + "Reason:", params
    )
    found = parse_change(raw)
    if not found:
        return dict(START)
    return {f: float(max(0, min(SEED_MAX, found[f]))) for f in FIELDS}


# --- what the scores do -------------------------------------------------------------


def standing_line(scores: dict, char: str, user: str) -> str:
    """How the numbers shape the character, for the prompt. Thresholds are what make
    closeness earned: secrets and romance are out of reach until the scores allow it."""
    t, a, r = (float(scores.get(f, START[f])) for f in FIELDS)
    parts = []
    if t < 25:
        parts.append(f"{char} does not trust {user} and keeps anything personal private")
    elif t < 60:
        parts.append(f"{char} is open with {user} about everyday things, and keeps secrets and old wounds private")
    else:
        parts.append(f"{char} trusts {user} and may share secrets and old wounds")
    if a < 25:
        parts.append("is cool and distant")
    elif a < 70 or t < 50:
        parts.append("is warm only as a friend would be")
    else:
        parts.append("may let romantic feelings show if the moment calls for it")
    if r < 25:
        parts.append(f"doubts {user}'s judgment")
    elif r >= 60:
        parts.append(f"takes {user}'s opinion seriously")
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] + "."


def gap_factor(scores: dict | None) -> float:
    """Writing first: someone fond of the user writes sooner, someone cold later."""
    a = float((scores or {}).get("affection", START["affection"]))
    return 0.75 if a >= 70 else 2.0 if a < 25 else 1.0


# --- storage ---------------------------------------------------------------------------


def get_scores(db, session_id: int, character_id: int) -> dict | None:
    from ..db.models import Standing

    row = db.get(Standing, (session_id, character_id))
    return {f: getattr(row, f) for f in FIELDS} if row else None


def save_scores(db, session_id: int, character_id: int, scores: dict, reason: str = "") -> None:
    from ..db.models import Standing

    row = db.get(Standing, (session_id, character_id))
    if row is None:
        row = Standing(session_id=session_id, character_id=character_id)
        db.add(row)
    for f in FIELDS:
        setattr(row, f, scores[f])
    if reason:
        row.last_reason = reason
    db.commit()


async def after_exchange(db, session, speaker, card, user_text: str, before: str, user_name: str) -> None:
    """Move the speaker's scores for what the user just did. Best-effort, and a
    misfire changes nothing."""
    scores = get_scores(db, session.id, speaker.id)
    if scores is None:
        scores = await seed(card, session.summary or "", user_name)
        save_scores(db, session.id, speaker.id, scores, "first impression")
    change = await score(user_text, before, speaker.name, user_name)
    if change:
        moved = apply_change(scores, change)
        unchanged = all(moved[f] == scores[f] for f in FIELDS)
        save_scores(db, session.id, speaker.id, moved, "" if unchanged else change["reason"])

