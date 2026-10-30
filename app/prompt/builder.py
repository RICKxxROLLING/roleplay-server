"""Prompt assembly. Built for MythoMax L2 13B (Alpaca); the instruct format is
now pluggable -- see formats.py -- while the slot ordering below is shared.

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
from .formats import ALPACA, PromptFormat, get_format
from .tokens import estimate_tokens

DEFAULT_SYSTEM = (
    "You are {{char}} in an ongoing roleplay with {{user}}. Stay in character at "
    "all times. Write vivid, immersive prose in third person, past tense. Describe "
    "{{char}}'s actions, speech and internal state. Advance the scene with concrete "
    "detail rather than summarising."
)

# Deliberately NOT part of DEFAULT_SYSTEM. A card's `system_prompt` replaces the
# default wholesale, per the V2 contract -- and community cards are authored
# against SillyTavern, which supplies its own persistent anti-impersonation
# directive. So card authors never write one, and folding ours into the default
# meant importing any card with a system_prompt silently dropped the only copy.
# Observed against a real model: the character narrated the user's actions and
# dialogue outright.
IMPERSONATION_GUARD = (
    "Write only {{char}}'s speech, actions and inner life. Never write {{user}}'s "
    "words or actions, and never decide what {{user}} does next -- end your reply "
    "and let {{user}} answer."
)

# Repeated immediately before the generation point. Small models weight recency
# far more heavily than a directive sitting thousands of tokens up the prompt,
# and impersonation is exactly the instruction they drop first.
TAIL_REMINDER = (
    "Continue as {{char}}. End on {{char}}'s own words or actions, at a moment "
    "{{user}} can respond to. Do not write {{user}}'s dialogue or actions."
)

# Short on purpose: the full narration style already sits in the head, and a
# long block here would crowd the card's own post_history_instructions.
STYLE_REMINDER = "Follow the narration style described above."

# Texting mode (chat/texting.py): the chat reimagined as a phone conversation.
# Replaces the prose directives rather than adding to them -- a card's
# system_prompt and the narration style both ask for prose, and a prompt asking
# for both gets prose with a text message buried in it.
TEXTING_SYSTEM = (
    "{{char}} and {{user}} are texting each other on their phones. You are {{char}}. "
    "Write only the texts {{char}} sends: the words they type, the way {{char}} really "
    "texts -- short and casual, in their own voice, with their own humor and habits."
)
TEXTING_TAIL = (
    "Reply as {{char}} with their next text to {{user}}: just the words they type, short "
    "and casual. One to three short messages, each on its own line."
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
    if user_name == "You":
        # The no-persona default. Cards are written for a name, so plain
        # substitution produces "the knock on You's door" -- seen live.
        for tag in ("{{user}}", "<USER>"):
            text = text.replace(f"{tag}'s", "your").replace(f"{tag}’s", "your")
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


def _scene_line(scene: dict | None) -> str:
    """One line, skipping fields nobody has filled in yet."""
    if not scene:
        return ""
    parts = [f"{k.capitalize()}: {v}" for k, v in scene.items()
             if not k.startswith("_") and v and v != "unknown"]
    return f"Current scene -- {'; '.join(parts)}." if parts else ""


def _format_turn(
    role: str, content: str, char: str, user: str, fmt: PromptFormat = ALPACA
) -> str:
    """One message. Speaker names stay in the content in every format: the
    newline-anchored user stop and the orchestrator's prefix strip rely on them."""
    if role == "user":
        return fmt.user.format(text=f"{user}: {content}")
    if role.startswith("other:"):
        # Another character in a group chat. User side, never assistant: the
        # speaker must not read a transcript in which it wrote as someone else.
        return fmt.user.format(text=f"{role[len('other:'):]}: {content}")
    return fmt.assistant.format(text=f"{char}: {content}")


# Said as what to do, not what to avoid -- see TAIL_REMINDER and CLAUDE.md.
GROUP_LINE = (
    "Also in this scene, each speaking for themselves in their own turns: {names}. "
    "Write {{{{char}}}}'s part only."
)


def build_prompt(
    card: CharacterCard,
    history: list[tuple[str, str]],
    user_name: str = "You",
    user_persona: str = "",
    summary: str = "",
    world: str = "",
    facts: str = "",
    lore_before: list[str] | None = None,
    lore_after: list[str] | None = None,
    retrieved: list[str] | None = None,
    pinned: list[str] | None = None,
    context_tokens: int = 4096,
    max_new_tokens: int = 400,
    reserve_tokens: int = 256,
    prompt_format: str | PromptFormat = "alpaca",
    narration_style: str = "",
    scene: dict | None = None,
    others: list[tuple[str, str]] | None = None,
    time_note: str = "",
    initiative: str = "",
    texting: bool = False,
    mode_note: str = "",
    texting_examples: bool = True,
) -> BuiltPrompt:
    """`history` is [(role, content), ...] oldest-first, excluding the pending reply.

    `others` is [(name, short description)] for the other characters of a group
    chat; their lines arrive in `history` as "other:Name" roles.
    """
    fmt = prompt_format if isinstance(prompt_format, PromptFormat) else get_format(prompt_format)
    char = card.name or "Character"
    budget = context_tokens - max_new_tokens - reserve_tokens
    # Server-wide house style. Like the guard it is appended rather than folded
    # into DEFAULT_SYSTEM, or every card with its own system_prompt would drop it.
    style = "" if texting else substitute(narration_style.strip(), char, user_name)

    # --- Mandatory head ---
    # The guard is appended rather than baked into the default, so it survives a
    # card supplying its own system_prompt.
    system = substitute(
        TEXTING_SYSTEM if texting else (card.system_prompt or DEFAULT_SYSTEM), char, user_name
    )
    head_parts = [system, substitute(IMPERSONATION_GUARD, char, user_name), style]

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

    # The world first: it is the setting the character description sits in.
    if world.strip():
        head_parts.append(f"Setting: {substitute(world.strip(), char, user_name)}")

    char_block = _character_block(card, char, user_name)
    if char_block:
        head_parts.append(char_block)

    for text in lore_after:
        head_parts.append(substitute(text, char, user_name))

    others = [(n, d) for n, d in (others or []) if n and n != char]
    if others:
        head_parts.append("\n".join(
            [substitute(GROUP_LINE.format(names=", ".join(n for n, _ in others)), char, user_name)]
            + [f"{n}: {substitute(d, n, user_name)}" for n, d in others if d]
        ))

    head = "\n\n".join(p for p in head_parts if p)
    used = estimate_tokens(head)

    # --- Optional: example dialogue (first to go under pressure) ---
    example = ""
    if card.mes_example and (texting_examples or not texting):
        candidate = substitute(card.mes_example, char, user_name).replace("<START>", "").strip()
        # Kept while a third of the budget is left for history. It used to go
        # at half, which a full persona plus lore crosses on an 8K window --
        # and the examples are what hold the narrator's voice. Measured on
        # Stheno with the Dina card: first-person words in the first reply
        # 0/0/0/1 with them, 11/0/16/13 without.
        if candidate and used + estimate_tokens(candidate) <= budget * 2 / 3:
            example = f"Example dialogue:\n{candidate}"
            used += estimate_tokens(example)

    # --- Phase 2 hook: rolling summary ---
    summary_block = ""
    if summary:
        summary_block = f"Story so far: {summary}"
        used += estimate_tokens(summary_block)

    # --- Facts: the specifics a prose summary drops ---
    facts = substitute(facts.strip(), char, user_name)
    facts_block = f"Known facts:\n{facts}" if facts else ""
    used += estimate_tokens(facts_block)

    # --- Pinned moments: chosen by the user, so they sit above recall ---
    pinned = [t for t in (pinned or []) if t.strip()]
    pinned_block = ""
    if pinned:
        lines = "\n".join(substitute(t, char, user_name) for t in pinned)
        pinned_block = f"Moments to keep in mind, in the order they happened:\n{lines}"
        used += estimate_tokens(pinned_block)

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
    # Wrapped as its own block; in Alpaca a bare paragraph between two
    # ### Response: headers confuses Llama-2 instruct tunes.
    # The card's own tail comes first; ours lands last, closest to generation.
    tail_parts = [
        substitute(card.post_history_instructions, char, user_name),
        # Where the story *is* right now belongs closest to generation: it is
        # the thing a small model most often loses (scene.py).
        _scene_line(scene),
        # Repeated here for the same recency reason as the reminder below.
        STYLE_REMINDER if style else "",
        # After a long silence, the hour and the gap (chat/autonomy.py) -- or,
        # for a message nobody asked for, why the character is writing at all.
        substitute(initiative or time_note, char, user_name),
        # The first reply after a switch between story and texting: where they are now.
        substitute(mode_note, char, user_name),
        substitute(TEXTING_TAIL if texting else TAIL_REMINDER, char, user_name),
    ]
    tail = fmt.tail.format(text="\n".join(p for p in tail_parts if p))
    used += estimate_tokens(tail)

    priming = f"{fmt.response_open}{char}:"
    used += estimate_tokens(priming)

    # --- History, newest-first until budget is spent ---
    kept: list[str] = []
    dropped = 0
    for role, content in reversed(history):
        turn = _format_turn(role, substitute(content, char, user_name), char, user_name, fmt)
        cost = estimate_tokens(turn)
        if used + cost > budget:
            dropped += 1
            continue
        used += cost
        kept.append(turn)
    kept.reverse()

    # Everything above the history is one preamble: for Alpaca that renders
    # exactly as before, and chat formats need it inside a single system turn.
    preamble = "\n\n".join(
        b for b in (head, example, summary_block, facts_block, pinned_block, retrieved_block) if b
    )
    sections = [fmt.system.format(text=preamble)]
    sections.extend(kept)
    # Always present now -- the tail carries the impersonation reminder even when
    # the card has no post_history_instructions of its own.
    sections.append(tail)
    sections.append(priming)

    return BuiltPrompt(
        text=fmt.joiner.join(sections),
        # Stop before the model starts writing the user's side of the scene.
        # Note: only the newline-anchored user label -- a bare "Name:" would
        # truncate legitimate prose like: she turned to Riley: "..."
        # In a group, the other characters' labels too: each speaks in its own turn.
        stop=[fmt.stops[0], f"\n{user_name}:", *(f"\n{n}:" for n, _ in others),
              *fmt.stops[1:]],
        used_tokens=used,
        dropped_messages=dropped,
        lore_entries=lore_count,
        retrieved_entries=len(retrieved),
    )
