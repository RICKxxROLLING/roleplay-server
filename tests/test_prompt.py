"""Alpaca prompt assembly, placeholder substitution and token budgeting."""
from __future__ import annotations

from app.cards.models import CharacterCard, LoreEntry
from app.prompt import build_prompt, estimate_tokens, substitute
from app.prompt.builder import INSTRUCTION, RESPONSE


def card(**kw) -> CharacterCard:
    base = dict(
        name="Seraphine",
        description="{{char}} is an archivist.",
        personality="Guarded.",
        scenario="{{user}} arrives at midnight.",
        first_mes="Hello, {{user}}.",
    )
    base.update(kw)
    return CharacterCard(**base)


def test_placeholders_substituted():
    assert substitute("{{char}} greets {{user}}", "Sera", "Riley") == "Sera greets Riley"
    assert substitute("<BOT> and <USER>", "Sera", "Riley") == "Sera and Riley"


def test_prompt_contains_card_blocks():
    p = build_prompt(card(), [], user_name="Riley")
    assert "Seraphine is an archivist." in p.text
    assert "Seraphine's personality: Guarded." in p.text
    assert "Scenario: Riley arrives at midnight." in p.text
    assert p.text.rstrip().endswith("Seraphine:")


def test_post_history_wrapped_as_instruction():
    """A bare paragraph between two ### Response: headers confuses Llama-2 tunes."""
    p = build_prompt(card(post_history_instructions="Stay in character."), [])
    assert f"{INSTRUCTION}\nStay in character." in p.text
    # and it sits after the history, closest to generation
    assert p.text.index("Stay in character.") > p.text.index("Seraphine is an archivist.")


def test_impersonation_guard_survives_a_card_system_prompt():
    """The bug this exists for, seen against a real model: the character wrote
    the user's dialogue.

    A card's `system_prompt` replaces the default wholesale per the V2 contract.
    The anti-impersonation rule used to live inside that default, so importing
    any card carrying its own system_prompt silently dropped it -- and community
    cards never supply one, because SillyTavern adds its own separately.
    """
    p = build_prompt(
        card(system_prompt="You are Seraphine. Be terse."), [], user_name="Riley"
    )
    assert "Be terse." in p.text, "card's own directive must still be honoured"
    assert "Never write Riley's words or actions" in p.text


def test_impersonation_guard_is_not_duplicated_by_default():
    p = build_prompt(card(), [], user_name="Riley")
    assert p.text.count("Never write Riley's words or actions") == 1


def test_reminder_sits_closest_to_generation():
    """Small models weight recency; the head directive is thousands of tokens away."""
    p = build_prompt(card(), [("user", "hi")], user_name="Riley")
    reminder = "Do not write Riley's dialogue or actions."
    assert reminder in p.text
    assert p.text.index(reminder) > p.text.index("Seraphine is an archivist.")
    # Nothing but the response priming may follow it.
    assert p.text[p.text.index(reminder) + len(reminder):].strip() == f"{RESPONSE}\nSeraphine:"


def test_card_tail_and_reminder_coexist():
    """The card's own post-history text must not be displaced by ours."""
    p = build_prompt(card(post_history_instructions="Stay in character."), [])
    assert "Stay in character." in p.text
    assert "Do not write" in p.text
    assert p.text.index("Stay in character.") < p.text.index("Do not write")
    # Still one instruction block, not two competing headers.
    assert p.text.count(INSTRUCTION) == 1


def test_stop_sequences_are_newline_anchored():
    """A bare 'Riley:' would truncate prose like: she turned to Riley: "..." """
    p = build_prompt(card(), [], user_name="Riley")
    assert INSTRUCTION in p.stop
    assert "\nRiley:" in p.stop
    assert "Riley:" not in p.stop


def test_history_formatted_as_alpaca_turns():
    p = build_prompt(card(), [("user", "hi"), ("assistant", "hello")], user_name="Riley")
    assert f"{INSTRUCTION}\nRiley: hi" in p.text
    assert f"{RESPONSE}\nSeraphine: hello" in p.text


def test_budget_trims_oldest_first():
    hist = [("user", f"Turn {i}: " + "padding words here " * 15) for i in range(200)]
    p = build_prompt(card(), hist, context_tokens=4096, max_new_tokens=400, reserve_tokens=256)

    assert p.used_tokens <= 4096 - 400 - 256
    assert p.dropped_messages > 0
    assert "Turn 199:" in p.text
    assert "Turn 0:" not in p.text


def test_summary_injected():
    p = build_prompt(card(), [], summary="They met in the rain.")
    assert "Story so far: They met in the rain." in p.text


def test_lore_positioned_around_character_block():
    p = build_prompt(
        card(),
        [],
        lore_before=["BEFORE FACT"],
        lore_after=["AFTER FACT"],
    )
    i_before = p.text.index("BEFORE FACT")
    i_char = p.text.index("Seraphine is an archivist.")
    i_after = p.text.index("AFTER FACT")
    assert i_before < i_char < i_after
    assert p.lore_entries == 2


def test_example_dialogue_dropped_under_pressure():
    """mes_example is the first thing sacrificed when context is tight."""
    big_example = "example line " * 500
    p = build_prompt(card(mes_example=big_example), [], context_tokens=1200)
    assert "Example dialogue:" not in p.text


def test_token_estimate_is_conservative():
    """Better to under-fill context than blow past it."""
    text = "a" * 360
    assert estimate_tokens(text) >= 100
