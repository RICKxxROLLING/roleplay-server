"""Typed lines get quotes and asterisks only when every word survives."""
from __future__ import annotations

import pytest
from conftest import sse_events

from app.chat.tidy import judge, needs_tidying


@pytest.mark.parametrize("line, formatted, want", [
    ("yeah okay ill try I smile", '"Yeah, okay, I\'ll try." *I smile.*', '"Yeah, okay, I\'ll try." *I smile.*'),
    # The model's own extra action after the last word is cut off.
    ("Hi I'm Riley", '"Hi, I\'m Riley." *I extend my hand, smiling.*', '"Hi, I\'m Riley."'),
    # Spacing may change: "lookaround" into "look around".
    ("I take a lookaround", "*I take a look around.*", "*I take a look around.*"),
    # A changed or dropped word keeps the line as typed.
    ("the waiter walks up tote my order", "*The waiter walks up and totes my order.*", ""),
    ("will do thanks and I dig in", '"Will do, thanks." *I dig in.*', ""),
    # An unclosed quote is refused.
    ("hey there", '"Hey there.', ""),
])
def test_judge(line, formatted, want):
    assert judge(line, formatted) == want


def test_lines_already_formatted_are_left_alone():
    assert not needs_tidying('*I wave.* "Hey."')
    assert not needs_tidying("")
    assert needs_tidying("hey whats up")


def test_a_typed_line_is_saved_with_its_marks(client, session, llm, monkeypatch):
    client.patch("/api/settings", json={"tidy_user_lines": True})
    real = llm.generate

    async def fmt(prompt, params):
        if "You format lines for a roleplay chat" in prompt:
            return ' "Thanks again, I\'ll see you Monday." *I wave.*'
        return await real(prompt, params)

    monkeypatch.setattr(llm, "generate", fmt, raising=False)
    sse_events(client.post(f"/api/sessions/{session}/messages", json={"content": "thanks again ill see you monday I wave"}))
    msgs = client.get(f"/api/sessions/{session}").json()["messages"]
    assert msgs[-2]["content"] == '"Thanks again, I\'ll see you Monday." *I wave.*'
    client.patch("/api/settings", json={"tidy_user_lines": False})
    sse_events(client.post(f"/api/sessions/{session}/messages", json={"content": "ok bye now"}))
    assert client.get(f"/api/sessions/{session}").json()["messages"][-2]["content"] == "ok bye now"


def test_a_wrong_format_keeps_the_line_as_typed(client, session, llm):
    client.patch("/api/settings", json={"tidy_user_lines": True})
    sse_events(client.post(f"/api/sessions/{session}/messages", json={"content": "hello there friend"}))
    assert client.get(f"/api/sessions/{session}").json()["messages"][-2]["content"] == "hello there friend"


@pytest.mark.parametrize("line, formatted, want", [
    # Seen live: the action in the middle quoted with the speech, 3 of 3 times.
    ("maybe, i dont really know anyone in town yet though. i rub the back of my neck who would we even talk to",
     '"Maybe, I don\'t really know anyone in town yet though. I rub the back of my neck. Who would we even talk to?"',
     '"Maybe, I don\'t really know anyone in town yet though." *I rub the back of my neck.* "Who would we even talk to?"'),
    ("i laugh quietly and take a sip. its really good", '*i laugh quietly and take a sip.* "It\'s really good."',
     '*I laugh quietly and take a sip.* "It\'s really good."'),
    # Speech that only opens with one of those verbs stays speech.
    ("i take it youre new here", '"I take it you\'re new here."', '"I take it you\'re new here."'),
])
def test_actions_come_out_of_speech_and_i_is_capital(client, session, llm, monkeypatch, line, formatted, want):
    real = llm.generate

    async def gen(prompt, params):
        if "You format lines for a roleplay chat" in prompt:
            return " " + formatted
        return await real(prompt, params)

    monkeypatch.setattr(llm, "generate", gen, raising=False)
    client.patch("/api/settings", json={"tidy_user_lines": True})
    sse_events(client.post(f"/api/sessions/{session}/messages", json={"content": line}))
    assert client.get(f"/api/sessions/{session}").json()["messages"][-2]["content"] == want


def test_an_action_quoted_as_speech_is_refused():
    assert judge("I nod and grab my coat ok see you", "\"I nod and grab my coat. 'Ok, see you.'\"") == ""
