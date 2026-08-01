"""Prompt assembly for MythoMax L2 13B (Alpaca instruction format).

Slot ordering follows the SillyTavern V2 contract, which is what makes community
cards behave the way their authors intended:

    1. system_prompt (card override) or our default RP directive
    2. Lore entries positioned "before_char"
    3. Character block: description + personality + scenario
    4. Lore entries positioned "after_char"
    5. Example dialogue (mes_example)   <- trimmed first under pressure
    6. Rolling summary
    7. Retrieved past turns             <- Phase 4; sits with the summary, not history
    8. Chat history                     <- trimmed oldest-first
    9. post_history_instructions (card) <- deliberately last, closest to output
   10. Response header priming the character's name

Budgeting is greedy from the top: mandatory blocks first, then history newest-first
until the budget is spent.

Retrieved turns go *above* the history rather than inline with it. They are out
of sequence by construction -- a line from twenty chapters ago -- and splicing
them into the recent-history block would present them as things that just
happened. Grouped under their own header they read as recollection, which is
what they are.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..cards.models import CharacterCard
from .tokens import estimate_tokens

DEFAULT_SYSTEM = (
    "You are {{char}} in an ongoing roleplay with {{user}}. Stay in character at "
    "all times. Write vivid, immersive prose in third person, past tense. Describe "
    "{{char}}'s actions, speech and internal state. Never write {{user}}'s dialogue "
    "or decide their actions. Advance the scene with concrete detail rather than "
    "summarising."
)

INSTRUCTION = "### Instruction:"
RESPONSE = "### Response:"


@dataclass
class BuiltPrompt:
    text: str
    stop: list[str]
    used_tokens: int
    dropped_messages: int
    lore_entries: int = 0
    retrieved_entries: int = 0


def substitute(text: str, char_name: str, user_name: str) -> str:
    """V2 cards use {{char}}/{{user}} (and legacy <BOT>/<USER>) placeholders."""
    if not text:
        return ""
    for a, b in (
        ("{{char}}", char_name),
        ("{{user}}", user_name),
        ("<BOT>", char_name),
        ("<USER>", user_name),
    ):
        text = text.replace(a, b)
    return text


def _character_block(card: CharacterCard, char: str, user: str) -> str:
    parts: list[str] = []
    if card.description:
        parts.append(substitute(card.description, char, user))
    if card.personality:
        parts.append(f"{char}'s personality: {substitute(card.personality, char, user)}")
    if card.scenario:
        parts.append(f"Scenario: {substitute(card.scenario, char, user)}")
    return "\n\n".join(parts)


def _format_turn(role: str, content: str, char: str, user: str) -> str:
    """One exchange in Alpaca form."""
    if role == "user":
        return f"{INSTRUCTION}\n{user}: {content}"
    return f"{RESPONSE}\n{char}: {content}"


def build_prompt(
    card: CharacterCard,
    history: list[tuple[str, str]],
    user_name: str = "You",
    user_persona: str = "",
    summary: str = "",
    lore_before: list[str] | None = None,
    lore_after: list[str] | None = None,
    retrieved: list[str] | None = None,
    context_tokens: int = 4096,
    max_new_tokens: int = 400,
    reserve_tokens: int = 256,
) -> BuiltPrompt:
    """`history` is [(role, content), ...] oldest-first, excluding the pending reply."""
    char = card.name or "Character"
    budget = context_tokens - max_new_tokens - reserve_tokens

    # --- Mandatory head ---
    system = substitute(card.system_prompt or DEFAULT_SYSTEM, char, user_name)
    head_parts = [system]

    if user_persona:
        head_parts.append(f"{user_name}'s persona: {substitute(user_persona, char, user_name)}")

    # Lorebook entries sit either side of the character definition, per the V2
    # `position` field. Already budget-capped by the lorebook itself, so they
    # go in before history competes for space.
    lore_before = lore_before or []
    lore_after = lore_after or []
    lore_count = len(lore_before) + len(lore_after)

    for text in lore_before:
        head_parts.append(substitute(text, char, user_name))

    char_block = _character_block(card, char, user_name)
    if char_block:
        head_parts.append(char_block)

    for text in lore_after:
        head_parts.append(substitute(text, char, user_name))

    head = "\n\n".join(p for p in head_parts if p)
    used = estimate_tokens(head)

    # --- Optional: example dialogue (first to go under pressure) ---
    example = ""
    if card.mes_example:
        candidate = substitute(card.mes_example, char, user_name).replace("<START>", "").strip()
        if candidate and used + estimate_tokens(candidate) < budget * 0.5:
            example = f"Example dialogue:\n{candidate}"
            used += estimate_tokens(example)

    # --- Phase 2 hook: rolling summary ---
    summary_block = ""
    if summary:
        summary_block = f"Story so far: {summary}"
        used += estimate_tokens(summary_block)

    # --- Phase 4 hook: retrieved past turns ---
    # Already capped by the retriever's own token budget, so it competes with
    # history for what's left rather than being trimmed again here.
    retrieved = [t for t in (retrieved or []) if t.strip()]
    retrieved_block = ""
    if retrieved:
        lines = "\n".join(substitute(t, char, user_name) for t in retrieved)
        retrieved_block = (
            "Relevant moments from earlier in this conversation, out of order:\n"
            f"{lines}"
        )
        used += estimate_tokens(retrieved_block)

    # --- Tail: post-history instructions sit closest to the generation point ---
    # Wrapped as a proper Alpaca instruction block; a bare paragraph between two
    # ### Response: headers confuses Llama-2 instruct tunes.
    tail = substitute(card.post_history_instructions, char, user_name)
    if tail:
        tail = f"{INSTRUCTION}\n{tail}"
        used += estimate_tokens(tail)

    priming = f"{RESPONSE}\n{char}:"
    used += estimate_tokens(priming)

    # --- History, newest-first until budget is spent ---
    kept: list[str] = []
    dropped = 0
    for role, content in reversed(history):
        turn = _format_turn(role, substitute(content, char, user_name), char, user_name)
        cost = estimate_tokens(turn)
        if used + cost > budget:
            dropped += 1
            continue
        used += cost
        kept.append(turn)
    kept.reverse()

    sections = [head]
    if example:
        sections.append(example)
    if summary_block:
        sections.append(summary_block)
    if retrieved_block:
        sections.append(retrieved_block)
    sections.extend(kept)
    if tail:
        sections.append(tail)
    sections.append(priming)

    return BuiltPrompt(
        text="\n\n".join(sections),
        # Stop before the model starts writing the user's side of the scene.
        # Note: only the newline-anchored user label -- a bare "Name:" would
        # truncate legitimate prose like: she turned to Riley: "..."
        stop=[INSTRUCTION, f"\n{user_name}:", "</s>"],
        used_tokens=used,
        dropped_messages=dropped,
        lore_entries=lore_count,
        retrieved_entries=len(retrieved),
    )
