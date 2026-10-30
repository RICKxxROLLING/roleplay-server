"""Group chats: several characters in one session, one speaker per reply."""
from __future__ import annotations

from conftest import sse_events

from app.prompt import build_prompt
from app.cards.models import CharacterCard

SIDE_TASKS = ("memory log", "fact sheet", "track the scene", "private journal")


def chat_prompts(llm):
    return [p for p in llm.prompts if not any(t in p.lower() for t in SIDE_TASKS)]


def make_char(client, name, **kw):
    r = client.post("/api/characters", json={"name": name, "first_mes": "Hey.", **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def group(client, owner, *members):
    r = client.post("/api/sessions", json={"character_id": owner, "member_ids": list(members)})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def say(client, sid, text, **kw):
    return sse_events(client.post(f"/api/sessions/{sid}/messages", json={"content": text, **kw}))


def messages(client, sid):
    return client.get(f"/api/sessions/{sid}").json()["messages"]


def test_single_character_chats_are_unchanged(client, session, llm):
    events = say(client, session, "Hello there.")
    assert not any(e["type"] == "speaker" for e in events)
    got = client.get(f"/api/sessions/{session}").json()
    assert [c["name"] for c in got["cast"]] == ["Seraphine"]
    assert got["messages"][-1]["speaker_name"] == "Seraphine"
    assert "Also in this scene" not in chat_prompts(llm)[-1]


def test_a_group_records_who_spoke(client, imported, llm):
    tommy = make_char(client, "Tommy", description="Joel's younger brother, a patrol leader.")
    sid = group(client, imported, tommy)
    got = client.get(f"/api/sessions/{sid}").json()
    assert [c["name"] for c in got["cast"]] == ["Seraphine", "Tommy"]
    assert got["title"] == "Chat with Seraphine & Tommy"

    events = say(client, sid, "Tommy, how was the patrol?")
    assert {"type": "speaker", "id": tommy, "name": "Tommy"} in events
    assert messages(client, sid)[-1]["speaker_name"] == "Tommy"
    # Tommy's own card writes the reply, and Seraphine is introduced as present.
    p = chat_prompts(llm)[-1]
    assert p.rstrip().endswith("Tommy:")
    assert "Also in this scene, each speaking for themselves in their own turns: Seraphine." in p
    assert "Write Tommy's part only." in p


def test_unnamed_turns_rotate_to_whoever_waited_longest(client, imported, llm):
    tommy = make_char(client, "Tommy")
    sid = group(client, imported, tommy)
    say(client, sid, "Morning, both of you.")  # Seraphine greeted, so Tommy is owed a turn
    say(client, sid, "So what now?")
    say(client, sid, "Fair enough.")
    assert [m["speaker_name"] for m in messages(client, sid) if m["role"] == "assistant"] == \
        ["Seraphine", "Tommy", "Seraphine", "Tommy"]


def test_the_user_can_pick_who_answers(client, imported, llm):
    tommy = make_char(client, "Tommy")
    sid = group(client, imported, tommy)
    say(client, sid, "Anyone?", speaker_id=imported)
    assert messages(client, sid)[-1]["speaker_name"] == "Seraphine"
    # And a character can answer without a new user message.
    sse_events(client.post(f"/api/sessions/{sid}/respond", json={"character_id": tommy}))
    msgs = messages(client, sid)
    assert [m["role"] for m in msgs[-3:]] == ["user", "assistant", "assistant"]
    assert msgs[-1]["speaker_name"] == "Tommy"


def test_a_stranger_cannot_be_made_to_answer(client, imported, session, llm):
    stranger = make_char(client, "Stranger")
    r = client.post(f"/api/sessions/{session}/respond", json={"character_id": stranger})
    assert r.status_code == 400


def test_other_characters_lines_are_labelled_on_the_user_side(client, imported, llm):
    tommy = make_char(client, "Tommy")
    sid = group(client, imported, tommy)
    say(client, sid, "Tommy?")  # Tommy answers
    p = client.get(f"/api/sessions/{sid}/prompt", params={"speaker_id": imported}).json()
    assert p["speaker"] == "Seraphine"
    # Llama 3 would show it; with the default Alpaca format the user header is
    # the "### Instruction:" block -- Tommy's line sits in one, labelled.
    assert "### Instruction:\nTommy: *She nods once" in p["prompt"]
    assert "\nTommy:" in p["stop"] and "\nSeraphine:" not in p["stop"]


def test_a_swipe_is_rerolled_by_the_same_speaker(client, imported, llm):
    tommy = make_char(client, "Tommy")
    sid = group(client, imported, tommy)
    say(client, sid, "Tommy, you there?")
    events = sse_events(client.post(f"/api/sessions/{sid}/regenerate"))
    assert {"type": "speaker", "id": tommy, "name": "Tommy"} in events
    last = messages(client, sid)[-1]
    assert last["speaker_name"] == "Tommy" and last["swipes"] == 2


def test_the_guard_covers_the_other_characters(client, imported, llm):
    tommy = make_char(client, "Tommy")
    sid = group(client, imported, tommy)
    llm.replies = ['*Seraphine sets down the lamp.* "Long night." *Tommy shrugs and walks off.*']
    events = say(client, sid, "Hi Seraphine.")
    assert any(e["type"] == "guard" for e in events)
    text = messages(client, sid)[-1]["content"]
    assert "lamp" in text and "Tommy shrugs" not in text


def test_members_can_change_mid_chat(client, imported, llm):
    tommy = make_char(client, "Tommy")
    ellie = make_char(client, "Ellie")
    sid = group(client, imported, tommy)
    say(client, sid, "Tommy, hi.")
    r = client.put(f"/api/sessions/{sid}/members", json={"member_ids": [ellie]})
    assert [c["name"] for c in r.json()["cast"]] == ["Seraphine", "Ellie"]
    # Tommy left, but his line keeps his name.
    assert messages(client, sid)[-1]["speaker_name"] == "Tommy"


def test_deleting_a_member_leaves_its_lines(client, imported, llm):
    tommy = make_char(client, "Tommy")
    sid = group(client, imported, tommy)
    say(client, sid, "Tommy, hi.")
    assert client.delete(f"/api/characters/{tommy}").status_code == 200
    got = client.get(f"/api/sessions/{sid}").json()
    assert [c["name"] for c in got["cast"]] == ["Seraphine"]
    assert got["messages"][-1]["speaker_name"] == "Someone"


def test_folds_label_each_speaker(client, imported, llm):
    tommy = make_char(client, "Tommy")
    sid = group(client, imported, tommy)
    for i in range(8):
        say(client, sid, f"Turn {i}: " + "the snow keeps falling on the walls. " * 12)
    assert llm.summary_calls, "the fold never ran"
    assert "\nTommy: *She nods once" in llm.summary_calls[0]


def test_builder_renders_others_and_their_stops():
    built = build_prompt(
        card=CharacterCard(name="Dina"),
        history=[("user", "Hi."), ("other:Tommy", "Hey, Riley.")],
        user_name="Riley",
        prompt_format="llama3",
        others=[("Tommy", "Joel's younger brother.")],
    )
    assert "<|start_header_id|>user<|end_header_id|>\n\nTommy: Hey, Riley." in built.text
    assert "Tommy: Joel's younger brother." in built.text
    assert "\nTommy:" in built.stop
