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
    chat_prompts = [p for p in llm.prompts if "track the scene" not in p.lower() and "private journal" not in p.lower()]
    assert "reply 1." not in chat_prompts[-1]


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


def test_a_swipe_cannot_recall_the_reply_it_replaces(client, session, llm, embedder):
    """Retrieval skips what is already in the prompt, and a swipe takes the replaced
    reply out of the prompt -- so it became a recall candidate, and the model copied
    it back word for word. Seen live: a first regenerate nearly identical to the original."""
    from conftest import enable_retrieval, sse_events

    enable_retrieval(client)
    llm.replies = ["The brass ledger is hidden under the loose floorboard by the stove"]
    sse_events(client.post(f"/api/sessions/{session}/messages",
                           json={"content": "Where is the brass ledger hidden, under the floorboard by the stove?"}))
    llm.prompts.clear()
    llm.replies = ["Somewhere else entirely"]
    sse_events(client.post(f"/api/sessions/{session}/regenerate"))
    chat = [p for p in llm.prompts if "track the scene" not in p.lower() and "private journal" not in p.lower()]
    assert chat and "loose floorboard" not in chat[0]
