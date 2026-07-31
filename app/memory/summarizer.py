"""Rolling summarisation (Phase 2).

This is a *fold*, not an append: each pass rewrites the whole summary to absorb
new events. That's what keeps it bounded -- an append-only log would grow until
it ate the context window, defeating the purpose.

Quality here is mostly prompt quality. The instruction leans hard on preserving
concrete, retrievable detail (names, promises, unresolved threads) because that
is exactly what naive summarisation throws away first.
"""
from __future__ import annotations

from ..cards.models import CharacterCard
from ..config import settings
from ..llm import GenerationParams, get_client

SUMMARY_PROMPT = """### Instruction:
You maintain the memory log for an ongoing roleplay between {user} and {char}.

Rewrite the memory log so it absorbs the new events below. Preserve, in priority order:
1. Concrete facts: names, places, objects, numbers, titles.
2. Decisions made, promises given, and threads left unresolved.
3. How the relationship between {char} and {user} has shifted.
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


def _clean(text: str) -> str:
    """Strip artefacts small models tack onto summaries."""
    out = text.strip()
    for marker in ("### Instruction:", "### Response:", "EXISTING MEMORY LOG:", "NEW EVENTS:"):
        idx = out.find(marker)
        if idx != -1:
            out = out[:idx]
    # Models sometimes label the output despite being told not to.
    for prefix in ("MEMORY LOG:", "Memory log:", "Summary:", "SUMMARY:"):
        if out.startswith(prefix):
            out = out[len(prefix) :]
    return out.strip()


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
        max_new_tokens=settings.summary_max_tokens,
        stop=["### Instruction:", "</s>"],
    )

    raw = await get_client().generate(prompt, params)
    cleaned = _clean(raw)
    return cleaned if len(cleaned) >= 40 else existing
