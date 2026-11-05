"""World chats: people come and go, and what's said to one stays with them."""
from __future__ import annotations

import pytest
from conftest import sse_events

from app.chat import encounters
from app.db import SessionLocal
from app.db.models import ChatSession

SIDE_TASKS = ("memory log", "fact sheet", "track the scene", "private journal", "you are scoring",
              "will remember about it", "where would", "setting the scene")


def chat_prompts(llm):
    return [p for p in llm.prompts if not any(t in p.lower() for t in SIDE_TASKS)]


@pytest.fixture
def world(client):
    w = client.post("/api/worlds", json={"name": "Jackson", "description": "A walled town."}).json()
    people = {}
    for name, desc in (("Hana Brandt", "Hana keeps the general store."), ("Cole Weaver", "Cole mends fences.")):
        cid = client.post("/api/characters", json={
            "name": name, "description": desc, "first_mes": "GREETING-TEXT", "scenario": "SCENARIO-TEXT"}).json()["id"]
        client.put(f"/api/worlds/membership/{cid}", json={"world_id": w["id"]})
        people[name.split()[0]] = cid
    return {"id": w["id"], **people}


def start(client, world, who="Hana", persona=None):
    r = client.post("/api/sessions/world", json={"world_id": world["id"], "character_id": world[who],
                                                 "place": "the general store", "persona_id": persona})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def get(client, sid):
    return client.get(f"/api/sessions/{sid}").json()


def respond(client, sid):
    return sse_events(client.post(f"/api/sessions/{sid}/respond", json={}))


def say(client, sid, text):
    return sse_events(client.post(f"/api/sessions/{sid}/messages", json={"content": text}))


def walk(client, sid, who, world, place="the road"):
    r = client.post(f"/api/sessions/{sid}/walk-on", json={"place": place, "character_id": world[who]})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_world_chat_opens_on_someone_who_speaks_first(client, world, llm):
    sid = start(client, world)
    got = get(client, sid)
    assert got["world_chat"] and got["character_name"] == "Hana Brandt"
    assert got["awaiting_opening"]
    assert [m["role"] for m in got["messages"]] == ["narrator"]
    assert got["encounters"][0]["place"] == "the general store"

    events = respond(client, sid)
    assert any(e["type"] == "speaker" and e["name"] == "Hana Brandt" for e in events)
    prompt = chat_prompts(llm)[-1]
    assert "have never spoken before" in prompt and "speaks first" in prompt
    assert "GREETING-TEXT" not in prompt and "SCENARIO-TEXT" not in prompt
    got = get(client, sid)
    assert not got["awaiting_opening"]
    assert got["messages"][-1]["speaker_name"] == "Hana Brandt"


def test_the_host_is_nowhere_to_be_seen(client, world):
    sid = start(client, world)
    host_id = get(client, sid)["character_id"]
    assert all(c["id"] != host_id for c in client.get("/api/characters").json())
    roster = next(w for w in client.get("/api/worlds").json() if w["id"] == world["id"])
    assert all(c["id"] != host_id for c in roster["characters"])
    assert client.delete(f"/api/characters/{host_id}").status_code == 400
    assert [c["name"] for c in get(client, sid)["cast"]] == ["Hana Brandt"]
    assert client.get("/api/sessions").json()[0]["character_name"] == "Jackson"
    # One host however many chats there are.
    assert get(client, start(client, world, "Cole"))["character_id"] == host_id


def test_what_is_said_to_one_person_stays_with_them(client, world, llm):
    sid = start(client, world)
    respond(client, sid)
    say(client, sid, "I need antibiotics for WHISPERWORD and nobody can know.")
    llm.replies = []
    walk(client, sid, "Cole", world)
    respond(client, sid)
    prompt = chat_prompts(llm)[-1]
    assert "Cole Weaver:" in prompt
    assert "WHISPERWORD" not in prompt and "Hana" not in prompt.split("Cole mends fences.")[1]
    with SessionLocal() as db:
        s = db.get(ChatSession, sid)
        assert not s.summary and not s.facts
        assert s.encounters[0].ended and not s.encounters[1].ended


def test_someone_you_return_to_remembers_only_their_own_meeting(client, world, llm, monkeypatch):
    sid = start(client, world)
    respond(client, sid)
    say(client, sid, "I traded my father's silver watch for antibiotics.")

    real = llm.generate

    async def noting(prompt, params):
        if "will remember about it" in prompt:
            return "Riley came for antibiotics for a friend.\n- Hana holds the silver watch for three days."
        return await real(prompt, params)

    monkeypatch.setattr(llm, "generate", noting, raising=False)
    walk(client, sid, "Cole", world)
    respond(client, sid)
    say(client, sid, "Nice fence. Tell nobody about GATEWORD.")
    walk(client, sid, "Hana", world, "the general store")
    respond(client, sid)
    prompt = chat_prompts(llm)[-1]
    assert "Hana holds the silver watch" in prompt
    assert "have met before" in prompt and "have never spoken before" not in prompt
    assert "GATEWORD" not in prompt
    rows = {p["name"]: p for p in client.get(f"/api/sessions/{sid}/people").json()}
    assert rows["Hana Brandt"]["meetings"] == 2 and "silver watch" in rows["Hana Brandt"]["notes"]


def test_a_note_can_be_corrected_and_reaches_the_prompt(client, world, llm):
    sid = start(client, world)
    respond(client, sid)
    r = client.put(f"/api/sessions/{sid}/people/{world['Hana']}", json={"notes": "- Dina is Riley's friend."})
    assert r.status_code == 200
    assert client.put(f"/api/sessions/{sid}/people/{world['Cole']}", json={"notes": "x"}).status_code == 404
    say(client, sid, "Hello again.")
    assert "Dina is Riley's friend" in chat_prompts(llm)[-1]


def test_a_note_line_naming_someone_who_never_came_up_is_dropped():
    assert encounters._supported("Hana holds the watch for Riley.", "Hana and Riley spoke of a watch")
    assert not encounters._supported("Hana sold it to Marcus.", "Hana and Riley spoke of a watch")


def test_a_goodbye_gets_the_goodbye_line_and_lights_up_walk_on(client, world, llm):
    sid = start(client, world)
    respond(client, sid)
    say(client, sid, '"Thanks, Hana." *I head for the door.*')
    assert "says one brief goodbye" in chat_prompts(llm)[-1]
    assert get(client, sid)["leaving"]
    say(client, sid, '"Actually, one more thing. Do you sell rope?"')
    assert "says one brief goodbye" not in chat_prompts(llm)[-1]
    assert not get(client, sid)["leaving"]
    # The new person's opening isn't answering a goodbye said to someone else.
    say(client, sid, '"Goodbye, Hana."')
    walk(client, sid, "Cole", world)
    respond(client, sid)
    assert "says one brief goodbye" not in chat_prompts(llm)[-1]
    assert not get(client, sid)["leaving"]


@pytest.mark.parametrize("text", [
    # Written after the rule, in other words, as a check on it.
    '*I wave and carry on down the road.*', '"Bye, then."', '"Farewell, friend."',
    "I head home.", '*I set off toward the river.*', '"Take care of yourself. See you soon."',
    '"I had better get going."', 'Riley takes his leave.',
])
def test_leaving_is_noticed(text):
    assert encounters.is_leaving(text)


@pytest.mark.parametrize("text", [
    '"Do you think I should go to Seattle?"', '"Goodbye is the hardest word, my mother used to say."',
    '"Would you ever leave Jackson?"', '*I walk over to the shelf.*', '"Who walks this road at night?"',
    "I would never leave her.", '"She said goodbye to him years ago."', '*I step closer.*', "",
])
def test_staying_is_not_leaving(text):
    assert not encounters.is_leaving(text)


def test_swiping_an_opening_keeps_it_an_opening(client, world, llm):
    sid = start(client, world)
    respond(client, sid)
    reason = None
    with SessionLocal() as db:
        reason = db.get(ChatSession, sid).encounters[0].reason
    sse_events(client.post(f"/api/sessions/{sid}/regenerate", json={}))
    prompt = chat_prompts(llm)[-1]
    assert "speaks first" in prompt and reason.replace("{{user}}", "You") in prompt
    last = get(client, sid)["messages"][-1]
    assert last["swipes"] == 2 and last["speaker_name"] == "Hana Brandt"


def test_a_persona_line_about_someone_else_only_reaches_them(client, world, llm):
    dina = client.post("/api/characters", json={"name": "Dina", "description": "Dina patrols."}).json()["id"]
    client.put(f"/api/worlds/membership/{dina}", json={"world_id": world["id"]})
    world["Dina"] = dina
    persona = client.post("/api/personas", json={
        "name": "Riley", "description": "Riley is a quiet man. He has known Dina since childhood. He likes books."}).json()["id"]
    sid = start(client, world, persona=persona)
    respond(client, sid)
    prompt = chat_prompts(llm)[-1]
    assert "He likes books" in prompt and "since childhood" not in prompt
    walk(client, sid, "Dina", world)
    respond(client, sid)
    assert "since childhood" in chat_prompts(llm)[-1]


def test_a_card_written_around_the_user_is_not_a_stranger(client, world, llm):
    cid = client.post("/api/characters", json={"name": "Mara", "description": "Mara is {{user}}'s sister."}).json()["id"]
    client.put(f"/api/worlds/membership/{cid}", json={"world_id": world["id"]})
    world["Mara"] = cid
    sid = start(client, world, "Mara")
    respond(client, sid)
    prompt = chat_prompts(llm)[-1]
    assert "never spoken" not in prompt and "speaks first" in prompt


def test_a_failed_walk_on_leaves_you_where_you_were(client, world, llm, monkeypatch):
    sid = start(client, world)
    respond(client, sid)
    say(client, sid, "Hello.")
    before = get(client, sid)

    async def down(prompt, params):
        raise RuntimeError("backend down")

    monkeypatch.setattr(llm, "generate", down, raising=False)
    with pytest.raises(RuntimeError):
        client.post(f"/api/sessions/{sid}/walk-on", json={"character_id": world["Cole"]})
    after = get(client, sid)
    assert after["character_name"] == "Hana Brandt"
    assert len(after["messages"]) == len(before["messages"]) and len(after["encounters"]) == 1


def test_nobody_in_the_world_says_so(client):
    w = client.post("/api/worlds", json={"name": "Empty"}).json()
    r = client.post("/api/sessions/world", json={"world_id": w["id"]})
    assert r.status_code == 400 and "nobody" in r.json()["detail"].lower()
    assert client.get("/api/sessions").json() == []


def test_the_person_you_just_left_is_not_next(client, world):
    sid = start(client, world)
    for _ in range(4):
        r = client.post(f"/api/sessions/{sid}/walk-on", json={}).json()
        names = [e["name"] for e in r["encounters"]]
    assert all(a != b for a, b in zip(names, names[1:]))


def test_a_place_favours_whoever_belongs_there(client, world):
    # With a third person, so Hana isn't simply the only one left.
    cid = client.post("/api/characters", json={"name": "Tess", "description": "Tess bakes bread."}).json()["id"]
    client.put(f"/api/worlds/membership/{cid}", json={"world_id": world["id"]})
    sid = start(client, world, "Cole")
    for _ in range(6):
        r = client.post(f"/api/sessions/{sid}/walk-on", json={"place": "the general store"}).json()
        assert r["encounters"][-1]["name"] == "Hana Brandt"
        r = client.post(f"/api/sessions/{sid}/walk-on", json={"character_id": world["Cole"]}).json()


def test_deleting_the_person_leaves_the_chat_to_walk_on(client, world):
    sid = start(client, world)
    respond(client, sid)
    assert client.delete(f"/api/characters/{world['Hana']}").status_code == 200
    got = get(client, sid)
    assert got["cast"] == [] and not got["awaiting_opening"]
    assert client.post(f"/api/sessions/{sid}/messages", json={"content": "Hello?"}).status_code == 400
    r = client.post(f"/api/sessions/{sid}/walk-on", json={}).json()
    assert r["character_name"] == "Cole Weaver"


def test_a_world_with_chats_cant_be_deleted(client, world):
    sid = start(client, world)
    assert client.delete(f"/api/worlds/{world['id']}").status_code == 400
    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert client.delete(f"/api/worlds/{world['id']}").status_code == 200


def test_group_features_stay_out_of_a_world_chat(client, world):
    sid = start(client, world)
    assert client.patch(f"/api/sessions/{sid}", json={"mode": "texting"}).status_code == 400
    assert client.patch(f"/api/sessions/{sid}", json={"autonomous": True}).status_code == 400
    assert client.put(f"/api/sessions/{sid}/members", json={"member_ids": [world["Cole"]]}).status_code == 400
    assert client.post(f"/api/sessions/{sid}/guest", json={"character_id": world["Cole"]}).status_code == 400
    assert client.post(f"/api/sessions/{sid}/write-first", json={}).status_code == 400


def test_an_ordinary_chat_cant_walk_on(client, world):
    s = client.post("/api/sessions", json={"character_id": world["Hana"]}).json()["id"]
    assert client.post(f"/api/sessions/{s}/walk-on", json={}).status_code == 400
    got = get(client, s)
    assert not got["world_chat"] and got["encounters"] == [] and got["messages"][0]["content"] == "GREETING-TEXT"


def test_a_world_chat_survives_export_and_import(client, world):
    sid = start(client, world)
    respond(client, sid)
    client.put(f"/api/sessions/{sid}/people/{world['Hana']}", json={"notes": "- Hana holds the watch."})
    walk(client, sid, "Cole", world)
    respond(client, sid)
    data = client.get(f"/api/sessions/{sid}/export").json()
    assert data["version"] == 2 and data["world"] == "Jackson"

    new = client.post("/api/sessions/import", json=data).json()["id"]
    a, b = get(client, sid), get(client, new)
    assert b["world_chat"] and b["character_name"] == "Cole Weaver"
    assert [e["name"] for e in b["encounters"]] == [e["name"] for e in a["encounters"]]
    assert [m["content"] for m in b["messages"]] == [m["content"] for m in a["messages"]]
    assert {e["place"] for e in b["encounters"]} == {e["place"] for e in a["encounters"]}
    rows = {p["name"]: p for p in client.get(f"/api/sessions/{new}/people").json()}
    assert "holds the watch" in rows["Hana Brandt"]["notes"]
    # Reused rather than doubled.
    assert len([c for c in client.get("/api/characters").json() if c["name"] == "Hana Brandt"]) == 1


def test_recall_and_pins_only_reach_the_speakers_own_meetings(client, world):
    sid = start(client, world)
    respond(client, sid)
    say(client, sid, "A line for Hana.")
    walk(client, sid, "Cole", world)
    respond(client, sid)
    with SessionLocal() as db:
        s = db.get(ChatSession, sid)
        hana_lines = {m.id for m in encounters.messages_of(s, s.encounters[0])}
        cole_lines = {m.id for m in encounters.messages_of(s, s.encounters[1])}
        assert hana_lines and cole_lines and not hana_lines & cole_lines
        cole = s.encounters[1].character
        hana = s.encounters[0].character
        assert encounters.private_ids(s, cole) == hana_lines
        assert encounters.private_ids(s, hana) == cole_lines
