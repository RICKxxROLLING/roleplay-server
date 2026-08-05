"""Rolling summarisation (Phase 2).

This is a *fold*, not an append: each pass rewrites the whole summary to absorb
new events. That's what keeps it bounded -- an append-only log would grow until
it ate the context window, defeating the purpose.

Quality here is mostly prompt quality. The instruction leans hard on preserving
concrete, retrievable detail (names, promises, unresolved threads) because that
is exactly what naive summarisation throws away first.
"""
from __future__ import annotations

import re

from ..cards.models import CharacterCard
from ..config import settings
from ..llm import GenerationParams, get_client

SUMMARY_PROMPT = """### Instruction:
You maintain the memory log for an ongoing roleplay between {user} and {char}.

Rewrite the memory log so it absorbs the new events below. Preserve, in priority order:
1. Concrete facts: names, places, objects, numbers, titles.
2. Decisions made, promises given, and threads left unresolved.
3. What {char} and {user} did to each other and how they now stand.
4. {char}'s current goals, location, and emotional state.

Rules:
- Write {words} words of plain third-person past-tense prose.
- No headings, no bullet points, no preamble, no commentary about the summary itself.
- Do not invent events that are not in the log or the new events.
- Drop atmospheric description before dropping facts.

EXISTING MEMORY LOG:
{summary}

NEW EVENTS:
{transcript}

### Response:
"""

_TARGET_WORDS = "150-250"


def render_transcript(
    turns: list[tuple[str, str]], char_name: str, user_name: str
) -> str:
    lines = []
    for role, content in turns:
        speaker = user_name if role == "user" else char_name
        lines.append(f"{speaker}: {content}")
    return "\n".join(lines)


def build_summary_prompt(
    existing: str, turns: list[tuple[str, str]], char_name: str, user_name: str
) -> str:
    return SUMMARY_PROMPT.format(
        user=user_name,
        char=char_name,
        words=_TARGET_WORDS,
        summary=existing.strip() or "(nothing recorded yet)",
        transcript=render_transcript(turns, char_name, user_name),
    )


#: Stage directions -- "*She nods once.*" -- the clearest roleplay tell.
_STAGE_DIRECTION = re.compile(r"\*[^*\n]{3,}\*")
#: A leading "Elizabeth Rockwell:" speaker label.
_SPEAKER_LABEL = re.compile(r"^[A-Z][\w' .-]{1,40}:\s")


def _clean(text: str) -> str:
    """Strip artefacts small models tack onto summaries."""
    out = text.strip()
    for marker in ("### Instruction:", "### Response:", "EXISTING MEMORY LOG:", "NEW EVENTS:"):
        idx = out.find(marker)
        if idx != -1:
            out = out[:idx]
    # Models sometimes label the output despite being told not to.
    for prefix in ("MEMORY LOG:", "Memory log:", "Summary:", "SUMMARY:", "Updated report:"):
        if out.startswith(prefix):
            out = out[len(prefix) :]
    # A roleplay model may open with the character's own name as a speaker label.
    out = _SPEAKER_LABEL.sub("", out.strip(), count=1)
    return out.strip()


#: Mental-state verbs. A summary may record what the user *did*; asserting what
#: they knew or felt is deciding their interior life for them.
_MENTAL = (r"(?:knew|felt|thought|hoped|wondered|realis\w+|realiz\w+|understood|"
           r"sensed|believed|wanted|intended|feared|considered)")


def narrates_user(text: str, user_name: str) -> bool:
    """Whether the summary asserts what the *user* thought or felt.

    Seen live after many folds: "He knew that their paths would diverge",
    "Riley's thoughts turned to his own goals". It is the impersonation problem
    migrating into memory, and because the summary is re-injected every turn it
    quietly teaches the model that narrating the user is in bounds.

    Adding a rule to the fold prompt was tried and measured against the live
    model: it did not work. Given an already-contaminated log the model
    reproduces its opening almost verbatim, scoring 4.25 such phrases per
    summary before the rule and 5.50 after. From a clean log it produces none
    either way. So there is nothing for a rule to prevent and nothing it can
    repair -- the leverage is entirely in refusing to store the first one.

    Known gap, and a deliberate one: a pronoun reference in a later sentence
    ("Riley watched her go. He knew their paths would diverge.") is not caught.
    Catching it would mean flagging "He knew" in general, which is
    indistinguishable from the *character's* interiority -- and that has to stay
    allowed, or a roleplay summary could never say how the character felt. Real
    degraded summaries have carried the possessive form as well, so this catches
    them in practice without buying that false positive.
    """
    name = re.escape(user_name.strip())
    if not name:
        return False
    if re.search(rf"\b{name}'s (?:thoughts|feelings|mind|heart|hopes|fears)\b",
                 text, re.I):
        return True
    return re.search(rf"\b{name}\b[^.]{{0,40}}?\b{_MENTAL}\b", text, re.I) is not None


#: First and second person. A third-person past-tense summary has no business
#: containing these outside quoted speech, which is rejected separately.
_PERSONAL = re.compile(r"\b(?:I|me|my|mine|you|your|yours|we|us|our)\b", re.I)

#: A handful could be a quirk of phrasing; a cluster means the voice is wrong.
_PERSONAL_LIMIT = 3


def wrong_voice(text: str) -> bool:
    """Whether the summary is written in first or second person.

    A third failure mode, found only by running the chat: the fold came back as
    the *user* speaking, in character, directly to the character --

        "Elizabeth, remember that even when I am not physically present, my
        spirit will always be with you..."

    Fourteen first- and second-person pronouns in seventy-nine words, and every
    existing guard missed it. There were no stage directions, no quoted speech,
    no "Name:" label (the opening is a vocative comma, not a speaker tag) and no
    "Riley knew" construction. It was fluent third-person-free prose that
    happened to be a farewell speech, and it replaced a summary that had been
    carrying real facts.

    Person is the cheap invariant the other checks were dancing around: whatever
    else a summary is, it is written *about* these people, not *by* one of them.
    """
    return len(_PERSONAL.findall(text)) >= _PERSONAL_LIMIT


def looks_like_roleplay(text: str) -> bool:
    """Whether the model carried on the scene instead of summarising it.

    Roleplay finetunes are the ones most likely to be used here, and they are
    strongly biased toward staying in character -- asked to compress a scene,
    they sometimes just write more of it. Observed against a real model: an
    entire fold came back as stage directions and quoted dialogue, which then
    got injected as "Story so far" on every subsequent turn.

    What makes that failure vicious is that it is self-perpetuating. The next
    fold is handed the previous summary and told to rewrite it, so a summary
    that is dialogue produces more dialogue. Measured against the live model:
    a clean summary stayed clean whether the new turns were sparse or dense
    with narration, while a degraded one reproduced itself. The loop cannot
    start if a degraded summary is never stored, which is what this guards.
    """
    if _STAGE_DIRECTION.search(text):
        return True
    if _SPEAKER_LABEL.match(text.strip()):
        return True
    # Reproduced dialogue. Summaries of these chats came back with none at all,
    # so two or more quoted spans means it is transcribing rather than digesting.
    return text.count('"') >= 4


async def summarize(
    existing: str,
    turns: list[tuple[str, str]],
    card: CharacterCard,
    user_name: str,
) -> str:
    """Fold `turns` into `existing` and return the new memory log.

    Returns `existing` unchanged if the model gives us nothing usable -- losing
    the old summary because one call misfired would be far worse than skipping.
    """
    if not turns:
        return existing

    prompt = build_summary_prompt(existing, turns, card.name or "Character", user_name)
    params = GenerationParams(
        temperature=settings.summary_temperature,
        top_p=0.9,
        top_k=40,
        repeat_penalty=1.0,
        # The fold prompt carries a whole transcript slice and can outgrow the
        # chat prompt, so it needs the same explicit window -- otherwise the
        # backend truncates the instructions off the front and the model is
        # left with bare dialogue to continue.
        context_tokens=settings.context_tokens,
        max_new_tokens=settings.summary_max_tokens,
        stop=["### Instruction:", "</s>"],
    )

    raw = await get_client().generate(prompt, params)
    cleaned = _clean(raw)

    if len(cleaned) < 40:
        return existing
    # Rejecting leaves the watermark where it is, so the same turns are folded
    # again next time against a larger transcript -- and a larger transcript is
    # exactly what pulls the model back toward summarising. Retrying is the
    # recovery path, not just a safety net.
    if looks_like_roleplay(cleaned):
        return existing
    if wrong_voice(cleaned):
        return existing
    if narrates_user(cleaned, user_name):
        return existing
    return cleaned
