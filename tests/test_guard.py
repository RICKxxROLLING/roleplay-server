"""Stripping narration of the user's actions before a reply is stored.

Cases are taken from real Stheno 8B transcripts. Both directions matter: a miss
seeds the feedback loop, a false positive deletes the character's prose.
"""
from __future__ import annotations

from app.chat.guard import strip_user_actions


def strip(text: str) -> str:
    return strip_user_actions(text, "Riley", "Dina")[0]


# --- removed ----------------------------------------------------------------

def test_user_action_sentence_removed():
    out = strip("*Dina leans back.* *Riley nods.* \"Good.\" *she says.*")
    assert "Riley nods" not in out
    assert out.startswith("*Dina leans back.*")


def test_user_sentence_inside_a_longer_span():
    text = ("*Riley chuckles as he begins fiddling with the radio. "
            "Dina leans against the workbench, watching him.*")
    assert strip(text) == "*Dina leans against the workbench, watching him.*"


def test_user_dialogue_after_their_action_removed():
    text = ('*Riley smiles warmly and sets the quilt down.* "Hey there, little man."\n\n'
            '*She clears her throat.* "So, what have you been working on?"')
    out = strip(text)
    assert "little man" not in out
    assert out == '*She clears her throat.* "So, what have you been working on?"'


def test_user_dialogue_before_an_attribution_removed():
    text = '"Looks like Tommy has a crew," Riley observes, pointing at the wall. Dina squints.'
    out = strip(text)
    assert "Tommy" not in out
    assert out == "Dina squints."


def test_opening_phrase_does_not_hide_the_subject():
    out = strip("*In the darkness, Riley reaches over and brushes her hair back.* \"Night.\"")
    assert "Riley reaches" not in out


def test_second_person_narration_removed():
    assert strip_user_actions("*You nod slowly.* *Dina grins.*", "You", "Dina")[0] == "*Dina grins.*"


def test_reports_how_many_sentences_went():
    assert strip_user_actions("*Riley nods. Riley grins. Dina sighs.*", "Riley", "Dina")[1] == 2


# --- kept -------------------------------------------------------------------

def test_speech_is_never_touched():
    text = '*Dina laughs.* "Riley nods at everything I say, you know."'
    assert strip(text) == text


def test_unstarred_dialogue_is_speech_when_narration_is_starred():
    """In asterisk style, plain text is what the character says aloud."""
    text = "*Dina shrugs.* Riley thinks he's funny. He isn't."
    assert strip(text) == text


def test_mentions_that_are_not_the_subject_survive():
    text = ("*She tosses it to Riley, admiring how his hand catches it. "
            "Riley's coat hangs by the door. As Riley sits, Dina pours the tea.*")
    assert strip(text) == text


def test_compound_subject_with_the_character_survives():
    assert strip("*Riley and Dina walk toward the stables.*") == "*Riley and Dina walk toward the stables.*"


def test_line_after_a_sentence_naming_the_character_is_hers():
    out = strip('*Riley nods, and Dina lets out a breath.* "Thanks. You\'re a lifesaver."')
    assert "lifesaver" in out


def test_attributed_line_after_removal_survives():
    out = strip('*Riley nods.* "Good," *she says, relieved.*')
    assert '"Good,"' in out


def test_paragraphs_and_spacing_are_preserved():
    text = ('*She looks up.* "Hey."\n\n*Riley nods.*\n\n'
            '*Dina steps inside.* "It hasn\'t changed."')
    assert strip(text) == '*She looks up.* "Hey."\n\n*Dina steps inside.* "It hasn\'t changed."'


def test_clean_reply_is_returned_unchanged():
    text = "*Dina smiles.* \"Hey, Riley.\""
    assert strip_user_actions(text, "Riley", "Dina") == (text, 0)


def test_plain_paragraphs_after_a_starred_one_are_still_scanned():
    """Seen live: a starred opening paragraph, then plain prose."""
    text = ('*Later that night, they sat by the fire.*\n\n'
            '"I still can\'t believe you came back," Riley said, turning to look at her.\n\n'
            '"It didn\'t seem like I had other options," Dina admitted.')
    out = strip(text)
    assert "can't believe" not in out
    assert "other options" in out
    assert out.startswith("*Later that night")


# --- first-person slips -----------------------------------------------------

from app.chat.guard import first_person_narration, slipped_into_first_person  # noqa: E402

THIRD = "*Dina stood on the porch, JJ bundled against her.* \"Hey.\""


def test_first_person_counts_narration_not_speech():
    assert first_person_narration('"I know. I really do. My fault," she says.') == 0
    assert first_person_narration("I take the mug, and my hands warm. I sigh.") == 3


def test_slip_detected_against_a_third_person_greeting():
    reply = "I take the mug from Riley, letting the warmth seep into my palms. I smile."
    assert slipped_into_first_person(reply, THIRD)
    assert not slipped_into_first_person('*She smiles.* "I missed this, my friend. I did."', THIRD)


def test_first_person_card_is_left_alone():
    greeting = "I look up as you walk in. My coffee has gone cold; I don't care."
    assert not slipped_into_first_person("I grin and wave you over to my table. I knew it.", greeting)


# --- the user's lines the guard used to miss (seen live, Stheno 8B) ------------

def test_quote_in_the_next_paragraph_after_the_users_action_removed():
    text = ('*Dina brushes snow from his hair.* "Kiddo wants his dinner."\n\n'
            "Riley smiles and nods.\n\n"
            '"I\'d like that. You\'re always welcome here, okay?"\n\n'
            "His simple offer means more than Dina can express.")
    out = strip(text)
    assert "always welcome" not in out and "Riley smiles" not in out
    assert "Kiddo wants his dinner" in out and "simple offer" in out


def test_possessive_lead_in_with_a_quote_is_the_user_speaking():
    text = ("She held up her hands in mock defense.\n\n"
            "Riley's expression softened as he took in her tired features. "
            '"What are you doing here, anyway?"\n\n'
            "Dina looked away.")
    out = strip(text)
    assert "What are you doing here" not in out and "expression softened" not in out
    assert out.startswith("She held up her hands") and out.endswith("Dina looked away.")


def test_possessive_lead_in_starred():
    out = strip('*Riley\'s voice drops low.* "You came back because...?" *The fire crackles.*')
    assert "came back" not in out and "voice drops" not in out
    assert "The fire crackles" in out


# --- kept -----------------------------------------------------------------------

def test_possessive_without_a_quote_is_kept():
    text = "Riley's eyes were kind, and that was the worst part. Dina looked away."
    assert strip(text) == text


def test_possessive_of_a_thing_is_kept():
    text = 'Riley\'s cabin stood at the edge of town. "Home sweet home," Dina muttered.'
    assert strip(text) == text


def test_possessive_followed_by_her_own_attributed_line_is_kept():
    text = 'Riley\'s face fell. "I didn\'t mean it like that," she said quickly.'
    assert strip(text) == text


def test_possessive_that_hands_back_to_the_character_is_kept():
    text = "Riley's gaze met Dina's across the table. \"Don't look at me like that.\""
    assert strip(text) == text


def test_next_paragraph_quote_attributed_to_her_is_kept():
    text = 'Riley nods.\n\n"Then it\'s settled," she says.'
    assert strip(text) == '"Then it\'s settled," she says.'


def test_next_paragraph_quote_after_her_own_sentence_is_kept():
    text = 'Riley nods. Dina sighs and turns away.\n\n"We leave at dawn."'
    assert strip(text) == 'Dina sighs and turns away.\n\n"We leave at dawn."'
