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


# --- instruct formats -------------------------------------------------------

def test_alpaca_is_the_default_and_unchanged_by_name():
    hist = [("user", "hi"), ("assistant", "hello")]
    assert build_prompt(card(), hist).text == build_prompt(card(), hist, prompt_format="alpaca").text


def test_unknown_format_falls_back_to_alpaca():
    """A bad stored value must not fail every turn."""
    assert build_prompt(card(), [], prompt_format="nope").text == build_prompt(card(), []).text


def test_llama3_wraps_every_block_in_its_template():
    p = build_prompt(
        card(post_history_instructions="Stay in character."),
        [("user", "hi"), ("assistant", "hello")],
        user_name="Riley",
        summary="They met in the rain.",
        prompt_format="llama3",
    )
    sys_open = "<|start_header_id|>system<|end_header_id|>\n\n"
    # One preamble turn carrying the card and memory, not a block per section.
    assert p.text.startswith(sys_open + "You are Seraphine")
    assert p.text.index("Story so far") < p.text.index("<|eot_id|>")
    assert "<|start_header_id|>user<|end_header_id|>\n\nRiley: hi<|eot_id|>" in p.text
    assert "<|start_header_id|>assistant<|end_header_id|>\n\nSeraphine: hello<|eot_id|>" in p.text
    # The tail is a late system turn, still closest to generation.
    assert p.text.rindex(sys_open) > p.text.index("Seraphine: hello")
    assert "Stay in character." in p.text[p.text.rindex(sys_open):]
    assert p.text.endswith("<|start_header_id|>assistant<|end_header_id|>\n\nSeraphine:")
    assert "###" not in p.text


def test_llama3_writes_no_bos():
    """Ollama adds BOS itself even with raw=true; writing one doubles it."""
    assert "<|begin_of_text|>" not in build_prompt(card(), [], prompt_format="llama3").text


def test_chat_formats_keep_the_newline_anchored_user_stop():
    for fmt, eos in (("llama3", "<|eot_id|>"), ("chatml", "<|im_end|>")):
        p = build_prompt(card(), [], user_name="Riley", prompt_format=fmt)
        assert eos in p.stop
        assert "\nRiley:" in p.stop
        assert "Riley:" not in p.stop
        assert INSTRUCTION not in p.stop


def test_chatml_shape():
    p = build_prompt(card(), [("user", "hi")], user_name="Riley", prompt_format="chatml")
    assert p.text.startswith("<|im_start|>system\n")
    assert "<|im_start|>user\nRiley: hi<|im_end|>\n" in p.text
    assert p.text.endswith("<|im_start|>assistant\nSeraphine:")


def test_narration_style_survives_a_card_system_prompt():
    p = build_prompt(
        card(system_prompt="Custom card prompt."),
        [],
        user_name="Riley",
        narration_style="Describe {{char}}'s surroundings for {{user}}.",
    )
    assert "Custom card prompt." in p.text
    assert "Describe Seraphine's surroundings for Riley." in p.text
    # Reminded in the tail, closest to generation, without repeating it whole.
    tail = p.text[p.text.rindex(INSTRUCTION):]
    assert "narration style" in tail
    assert "surroundings" not in tail


def test_blank_narration_style_adds_nothing():
    assert build_prompt(card(), [], narration_style="  ").text == build_prompt(card(), []).text


def test_no_persona_possessive_reads_naturally():
    assert substitute("The knock on {{user}}'s door", "Dina", "You") == "The knock on your door"
    assert substitute("<USER>’s hand", "Dina", "You") == "your hand"
    # Only the default: a real name keeps its possessive.
    assert substitute("{{user}}'s door", "Dina", "Riley") == "Riley's door"


def test_example_dialogue_survives_a_heavy_card_on_a_wide_window():
    """Examples anchor the narrator's voice; a big persona alone must not evict them."""
    persona = "Riley is a quiet mechanic. " * 400  # ~3000 tokens of head
    p = build_prompt(card(mes_example="{{char}}: Hello."), [], user_persona=persona,
                     context_tokens=8192, max_new_tokens=512, reserve_tokens=256)
    assert "Example dialogue:" in p.text


# --- calibrated token estimate -----------------------------------------------

def test_estimate_starts_cautious_and_learns_the_real_ratio():
    from app.prompt import tokens

    text = "x" * 4850
    assert estimate_tokens(text) == int(4850 / 3.6) + 1  # unmeasured: the cautious default
    tokens.observe("test-model", chars=24250, tokens=5000)  # 4.85 chars/token, Llama 3's
    ratio, measured = tokens.chars_per_token("test-model")
    assert measured and abs(ratio - 4.85 * tokens.SAFETY) < 1e-6


def test_one_odd_measurement_cannot_skew_budgeting():
    from app.prompt import tokens

    tokens.observe("test-model", chars=100000, tokens=1000)  # 100 chars/token: nonsense
    assert tokens.chars_per_token("test-model")[0] <= 6.0 * tokens.SAFETY
    tokens.observe("m2", chars=1000, tokens=50)  # too short to learn from
    assert tokens.chars_per_token("m2") == (tokens.CHARS_PER_TOKEN, False)


def test_ratios_are_per_model():
    from app.prompt import tokens

    tokens.observe("llama-ish", chars=4850 * 10, tokens=10000 // 1)
    assert tokens.chars_per_token("other")[1] is False
