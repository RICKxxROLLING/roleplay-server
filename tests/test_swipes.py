"""Regenerate keeps earlier versions of the last reply as swipes."""
from __future__ import annotations

from conftest import sse_events


def messages(client, session):
    return client.get(f"/api/sessions/{session}").json()["messages"]


def chat(client, session, text="hi"):
    sse_events(client.post(f"/api/sessions/{session}/messages", json={"content": text}))


def regenerate(client, session):
    return sse_events(client.post(f"/api/sessions/{session}/regenerate"))


def test_regenerate_adds_a_swipe_instead_of_replacing(client, session, llm):
    chat(client, session)
    first = messages(client, session)[-1]
    assert first["swipes"] == 1
    regenerate(client, session)
    msgs = messages(client, session)
    assert len(msgs) == 3  # greeting, user, one reply -- not a second reply
    last = msgs[-1]
    assert last["id"] == first["id"]
    assert (last["swipes"], last["swipe_index"]) == (2, 1)
    assert last["content"] == "*She nods once. reply 2.*"


def test_the_replaced_reply_is_not_in_the_regenerate_prompt(client, session, llm):
    chat(client, session)
    regenerate(client, session)
    assert "reply 1." not in llm.prompts[-1]


def test_swiping_back_selects_the_earlier_version(client, session, llm):
    chat(client, session)
    regenerate(client, session)
    mid = messages(client, session)[-1]["id"]
    r = client.post(f"/api/sessions/{session}/messages/{mid}/swipe", json={"index": 0})
    assert r.status_code == 200
    assert r.json()["content"] == "*She nods once. reply 1.*"
    assert messages(client, session)[-1]["swipe_index"] == 0


def test_only_the_latest_reply_can_be_swiped(client, session, llm):
    chat(client, session)
    regenerate(client, session)
    mid = messages(client, session)[-1]["id"]
    chat(client, session, "next")
    r = client.post(f"/api/sessions/{session}/messages/{mid}/swipe", json={"index": 0})
    assert r.status_code == 400


def test_out_of_range_swipe_is_refused(client, session, llm):
    chat(client, session)
    regenerate(client, session)
    mid = messages(client, session)[-1]["id"]
    assert client.post(f"/api/sessions/{session}/messages/{mid}/swipe", json={"index": 5}).status_code == 400


def test_editing_a_reply_edits_the_selected_swipe(client, session, llm):
    chat(client, session)
    regenerate(client, session)
    mid = messages(client, session)[-1]["id"]
    client.patch(f"/api/sessions/{session}/messages/{mid}", json={"content": "Edited."})
    client.post(f"/api/sessions/{session}/messages/{mid}/swipe", json={"index": 0})
    client.post(f"/api/sessions/{session}/messages/{mid}/swipe", json={"index": 1})
    assert messages(client, session)[-1]["content"] == "Edited."


def test_swipes_are_capped(client, session, llm):
    from app.chat.orchestrator import MAX_SWIPES

    chat(client, session)
    for _ in range(MAX_SWIPES + 3):
        regenerate(client, session)
    last = messages(client, session)[-1]
    assert last["swipes"] == MAX_SWIPES
    assert last["swipe_index"] == MAX_SWIPES - 1
