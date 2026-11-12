"""The second review's fixes, one test each."""
from __future__ import annotations

import datetime as dt

import pytest
from conftest import sse_events

from app.chat import encounters
from app.chat.orchestrator import _unnest
from app.chat.tidy import judge, needs_tidying
from app.memory.scene import moved
from app.memory.summarizer import grounded
from app.routes.stats import _streaks

SRC = "Riley: after the patrol we talked. Dina: my first plan failed, and both of us agree."


@pytest.mark.parametrize("fact", [
    "After the patrol, Dina and Riley talked.", "Both agree the plan failed.", "Initial plan failed, Dina agrees.",
])
def test_a_fact_opening_with_an_ordinary_word_is_kept(fact):
    assert grounded(fact, SRC)


def test_a_fact_naming_a_stranger_is_still_dropped():
    assert not grounded("After the patrol, Dina talked to Marcus.", SRC)


def test_a_pronoun_line_is_the_users_only_when_it_cant_be_the_characters():
    line = "She agreed to hold the basket until Sunday."
    assert not encounters.claims_about_user(line, "Riley", "he")
    assert not encounters.claims_about_user(line, "Riley", None)
    assert encounters.claims_about_user("He gave her a wooden bird feeder.", "Riley", "he")


@pytest.mark.parametrize("text", [
    '"I\'m going back to what you said about the lake."', '"It was good of you to come."',
])
def test_talk_that_isnt_a_goodbye(text):
    assert not encounters.is_leaving(text)


def test_heading_home_is_still_a_goodbye():
    assert encounters.is_leaving('"Right, I\'m heading home."')


@pytest.mark.parametrize("text, want", [
    ('"We can talk about the stables later."', False),
    ('"I\'ll walk you through the plan."', False),
    ('"Did you follow the tracks?"', False),
    ("I'll think about it later", False),
    ("*I'm going back to what you said about the lake.*", False),
    ("*I go back to the stables.*", True),
    ('"Let\'s go to the barn."', True),
    ('"Come on, I\'m heading over to the stables."', True),
    ("*Later, once JJ is asleep, I bring two mugs out to the porch.*", True),
    ("*I walk over to the stables.*", True),
    ("That evening I sit down next to her", True),
])
def test_what_moves_the_scene(text, want):
    assert moved(text) is want


def test_turning_back_after_a_goodbye_stays_with_them(client, llm):
    w = client.post("/api/worlds", json={"name": "Jackson"}).json()
    ids = {}
    for name in ("Hana Brandt", "Cole Weaver"):
        ids[name] = client.post("/api/characters", json={"name": name, "description": f"{name} lives here."}).json()["id"]
        client.put(f"/api/worlds/membership/{ids[name]}", json={"world_id": w["id"]})
    sid = client.post("/api/sessions/world", json={"world_id": w["id"], "character_id": ids["Hana Brandt"],
                                                   "place": "the store"}).json()["id"]
    sse_events(client.post(f"/api/sessions/{sid}/respond", json={}))
    sse_events(client.post(f"/api/sessions/{sid}/messages", json={"content": '"Goodbye, Hana."'}))
    for text in ('"Wait." *I walk back over.* "Did you say Tuesday?"', "*I walk back to Hana.*",
                 "*I turn around and walk to the counter.*"):
        sse_events(client.post(f"/api/sessions/{sid}/messages", json={"content": text}))
        assert len(client.get(f"/api/sessions/{sid}").json()["encounters"]) == 1
        sse_events(client.post(f"/api/sessions/{sid}/messages", json={"content": '"Goodbye again."'}))


def test_walk_on_waits_for_a_reply_in_progress(client, llm, monkeypatch):
    from app.chat import autonomy

    w = client.post("/api/worlds", json={"name": "Jackson"}).json()
    cid = client.post("/api/characters", json={"name": "Hana", "description": "Hana."}).json()["id"]
    client.put(f"/api/worlds/membership/{cid}", json={"world_id": w["id"]})
    sid = client.post("/api/sessions/world", json={"world_id": w["id"]}).json()["id"]
    monkeypatch.setattr(autonomy, "ACTIVE", 1)
    assert client.post(f"/api/sessions/{sid}/walk-on", json={}).status_code == 409
    monkeypatch.setattr(autonomy, "ACTIVE", 0)
    assert client.post(f"/api/sessions/{sid}/walk-on", json={}).status_code == 200


def test_the_streak_uses_the_viewers_day():
    local_today = dt.date(2026, 11, 10)
    days = {local_today - dt.timedelta(days=1), local_today - dt.timedelta(days=2)}
    # The server is already on the 11th; the viewer played yesterday, their time.
    assert _streaks(days, local_today) == (2, 2)


def test_other_alphabets_are_checked_and_paragraphs_left_alone():
    assert judge("привет I wave", '"Привет." *I wave.*') == '"Привет." *I wave.*'
    assert judge("привет I wave", '"Пока." *I wave.*') == ""
    assert not needs_tidying("first line\nsecond line")


def test_speech_wrapped_twice_is_unwrapped():
    assert _unnest("\"'Goodbye, Riley.'\" *He waves.*") == '"Goodbye, Riley." *He waves.*'
    assert _unnest('"She said \'no\' twice."') == '"She said \'no\' twice."'


def test_the_journal_is_told_whether_the_user_is_a_man(client, imported, llm):
    pid = client.post("/api/personas", json={"name": "Riley", "description": "He is quiet. He reads. He fixes radios."}).json()["id"]
    sid = client.post("/api/sessions", json={"character_id": imported, "persona_id": pid}).json()["id"]
    sse_events(client.post(f"/api/sessions/{sid}/messages", json={"content": "Hello."}))
    assert "private journal about Riley, a man." in llm.thought_calls[-1]
