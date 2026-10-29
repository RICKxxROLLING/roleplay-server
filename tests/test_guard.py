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
