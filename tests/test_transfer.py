"""Exporting a chat and bringing it back, here or on an install that has none of it."""
from __future__ import annotations

import base64
import copy

import pytest

from app.chat import transfer
from app.db import SessionLocal
from app.db.models import (Character, CharacterThought, ChatSession, Message, MessageEmbedding, Persona,
                           SessionMember, Standing, World)
from conftest import card_png, enable_retrieval


def say(client, sid, text="Hello."):
    with client.stream("POST", f"/api/sessions/{sid}/messages", json={"content": text}) as r:
        "".join(r.iter_text())


def export(client, sid):
    r = client.get(f"/api/sessions/{sid}/export")
    assert r.status_code == 200, r.text
    return r.json()


def bring_back(client, data):
    r = client.post("/api/sessions/import", json=data)
    assert r.status_code == 200, r.text
    return r.json()


def wipe():
    """As if this were another install: nothing from the exporting one remains."""
    with SessionLocal() as db:
        for model in (MessageEmbedding, CharacterThought, Standing, SessionMember, Message, ChatSession, Character,
                      World, Persona):
            db.query(model).delete()
        db.commit()


@pytest.fixture
def lived_in(client, llm):
    """A chat with a persona, a world, a group member, swipes, a pin, a fold and a journal."""
    wid = client.post("/api/worlds", json={"name": "Jackson", "description": "A walled town.",
                                           "entries": [{"keys": ["stables"], "content": "South of the gate."}]}).json()["id"]
    r = client.post("/api/characters/import", files={"file": ("c.png", card_png(), "image/png")})
    owner = r.json()["id"]
    client.put(f"/api/worlds/membership/{owner}", json={"world_id": wid})
    client.patch(f"/api/characters/{owner}", json={"rhythm": {"wake": "07:00", "sleep": "23:00"}})
    other = client.post("/api/characters", json={"name": "Tomas", "first_mes": "Hi.", "world_id": wid}).json()["id"]
    persona = client.post("/api/personas", json={"name": "Riley", "description": "A traveller."}).json()["id"]
    sid = client.post("/api/sessions", json={"character_id": owner, "persona_id": persona, "member_ids": [other],
                                             "title": "The archive", "narrator": True}).json()["id"]
    for i in range(10):
        say(client, sid, f"Line {i} about the ledger and the fire, at some length so memory folds.")
    with client.stream("POST", f"/api/sessions/{sid}/regenerate", json={}) as s:
        "".join(s.iter_text())
    with SessionLocal() as db:
        s = db.get(ChatSession, sid)
        s.autonomous = True
        s.messages[1].pinned = True
        s.scene = {"location": "the archive", "time": "night", "weather": "rain", "present": "Riley"}
        last = s.messages[-1]
        db.add(CharacterThought(session_id=sid, character_id=owner, message_id=last.id, journal="Odd night.", view="Wary."))
        db.merge(Standing(session_id=sid, character_id=owner, trust=61, affection=40, respect=52, last_reason="kept a promise"))
        s.summary, s.facts = "They searched the archive.", "- Seraphine keeps the key."
        s.summarized_upto_id = s.messages[5].id
        db.commit()
        assert last.alternatives
    return sid


# --- a copy on the same install -----------------------------------------------------


def test_the_prompt_is_identical_after_a_round_trip(client, lived_in):
    a = client.get(f"/api/sessions/{lived_in}/prompt").json()
    new = bring_back(client, export(client, lived_in))["id"]
    b = client.get(f"/api/sessions/{new}/prompt").json()
    assert a["prompt"] == b["prompt"] and a["stop"] == b["stop"]


def test_exporting_twice_says_the_same_thing(client, lived_in):
    first = export(client, lived_in)
    second = export(client, bring_back(client, first)["id"])
    for doc in (first, second):
        doc.pop("exported_at"), doc.pop("title")
    assert first == second


def test_it_makes_a_new_chat_and_changes_nothing_else(client, lived_in):
    before_chars = client.get("/api/characters").json()
    before_chat = client.get(f"/api/sessions/{lived_in}").json()
    got = bring_back(client, export(client, lived_in))
    assert got["id"] != lived_in and got["title"] == "The archive (imported)"
    assert got["characters_created"] == [] and set(got["characters_reused"]) == {"Seraphine", "Tomas"}
    assert client.get("/api/characters").json() == before_chars
    assert client.get(f"/api/sessions/{lived_in}").json() == before_chat


def test_a_card_edited_since_is_left_alone(client, lived_in):
    data = export(client, lived_in)
    cid = client.get(f"/api/sessions/{lived_in}").json()["character_id"]
    client.patch(f"/api/characters/{cid}", json={"description": "Changed since the export."})
    bring_back(client, data)
    assert client.get(f"/api/characters/{cid}").json()["card"]["description"] == "Changed since the export."


def test_writing_first_comes_back_off(client, lived_in):
    assert client.get(f"/api/sessions/{lived_in}").json()["autonomous"] is True
    new = bring_back(client, export(client, lived_in))["id"]
    assert client.get(f"/api/sessions/{new}").json()["autonomous"] is False


# --- another install ----------------------------------------------------------------


def test_everything_arrives_on_an_empty_install(client, lived_in):
    data = export(client, lived_in)
    before = client.get(f"/api/sessions/{lived_in}").json()
    wipe()
    got = bring_back(client, data)
    assert set(got["characters_created"]) == {"Seraphine", "Tomas"}
    assert got["worlds_created"] == ["Jackson"] and got["persona_created"] == "Riley"
    after = client.get(f"/api/sessions/{got['id']}").json()
    assert got["title"] == "The archive"
    assert [c["name"] for c in after["cast"]] == ["Seraphine", "Tomas"]
    strip = lambda ms: [{k: v for k, v in m.items() if k not in ("id", "speaker_id")} for m in ms]
    assert strip(after["messages"]) == strip(before["messages"])
    assert after["scene"]["location"] == "the archive" and after["summary"] == before["summary"]
    with SessionLocal() as db:
        s = db.get(ChatSession, got["id"])
        assert s.facts == data["facts"] and s.character.world.name == "Jackson"
        assert s.character.world.entries[0]["keys"] == ["stables"]
        assert s.character.rhythm == {"wake": "07:00", "sleep": "23:00"}
        assert s.character.avatar_path and sum(m.pinned for m in s.messages) == 1
        assert s.messages[-1].alternatives and s.summarized_upto_id == s.messages[data["folded"] - 1].id
        assert db.query(Standing).filter_by(session_id=s.id, character_id=s.character_id).one().trust == 61
        thought = db.query(CharacterThought).filter_by(session_id=s.id, journal="Odd night.").one()
        assert thought.message_id == s.messages[-1].id
    assert client.get(f"/api/characters/{got_id(client, got)}/avatar").status_code == 200


def got_id(client, got):
    return client.get(f"/api/sessions/{got['id']}").json()["character_id"]


def test_swipes_still_flip_after_arriving(client, lived_in):
    data = export(client, lived_in)
    wipe()
    new = bring_back(client, data)["id"]
    last = client.get(f"/api/sessions/{new}").json()["messages"][-1]
    assert last["swipes"] >= 2
    r = client.post(f"/api/sessions/{new}/messages/{last['id']}/swipe", json={"index": 0})
    assert r.status_code == 200, r.text


def test_the_chat_carries_on_after_arriving(client, llm, lived_in):
    data = export(client, lived_in)
    wipe()
    new = bring_back(client, data)["id"]
    count = len(client.get(f"/api/sessions/{new}").json()["messages"])
    say(client, new, "We pick up where we left off.")
    assert len(client.get(f"/api/sessions/{new}").json()["messages"]) >= count + 2


def test_recall_is_rebuilt_on_arrival(client, lived_in, embedder):
    enable_retrieval(client)
    new = bring_back(client, export(client, lived_in))["id"]
    with SessionLocal() as db:
        s = db.get(ChatSession, new)
        assert db.query(MessageEmbedding).filter_by(session_id=new).count() == len(s.messages)


def test_the_chat_still_arrives_if_embedding_is_down(client, lived_in, embedder):
    enable_retrieval(client)
    embedder.fail_with = RuntimeError("no embedding model")
    new = bring_back(client, export(client, lived_in))["id"]
    assert client.get(f"/api/sessions/{new}").status_code == 200


def test_vectors_are_not_in_the_file(client, lived_in):
    assert "vector" not in str(export(client, lived_in)).lower()


# --- lines whose speaker has gone ---------------------------------------------------


def test_someone_who_left_keeps_their_lines(client, lived_in):
    with SessionLocal() as db:
        s = db.get(ChatSession, lived_in)
        tomas = next(c for c in db.query(Character).all() if c.name == "Tomas")
        db.add(Message(session_id=s.id, role="assistant", content="*Tomas nods.*", speaker_id=tomas.id))
        db.commit()
    client.put(f"/api/sessions/{lived_in}/members", json={"member_ids": []})
    data = export(client, lived_in)
    assert next(p for p in data["characters"] if p["name"] == "Tomas")["member"] is False
    wipe()
    new = bring_back(client, data)["id"]
    after = client.get(f"/api/sessions/{new}").json()
    assert [c["name"] for c in after["cast"]] == ["Seraphine"]
    assert any(m["speaker_name"] == "Tomas" for m in after["messages"])


def test_a_deleted_characters_lines_stay_theirs(client, lived_in):
    with SessionLocal() as db:
        s = db.get(ChatSession, lived_in)
        tomas = next(c for c in db.query(Character).all() if c.name == "Tomas")
        db.add(Message(session_id=s.id, role="assistant", content="*Tomas nods.*", speaker_id=tomas.id))
        db.commit()
        tomas_id = tomas.id
    client.delete(f"/api/characters/{tomas_id}")
    new = bring_back(client, export(client, lived_in))["id"]
    line = next(m for m in client.get(f"/api/sessions/{new}").json()["messages"] if m["content"] == "*Tomas nods.*")
    assert line["speaker_name"] == "Someone"


# --- bad files ----------------------------------------------------------------------


@pytest.mark.parametrize("change,code", [
    (lambda d: d.update(format="roleplay-bundle"), 400),
    (lambda d: d.update(version=99), 400),
    (lambda d: d.update(character="Nobody"), 400),
    (lambda d: d["messages"][0].update(speaker="Nobody"), 400),
    (lambda d: d["messages"][0].update(alternatives=["only one"], alt_index=3), 400),
    (lambda d: d["messages"][0].update(role="system"), 422),
    (lambda d: d.update(folded=-1), 422),
    (lambda d: d["standings"][0].update(character="Nobody"), 400),
])
def test_a_bad_file_is_refused_and_writes_nothing(client, lived_in, change, code):
    data = copy.deepcopy(export(client, lived_in))
    change(data)
    before = len(client.get("/api/sessions").json())
    assert client.post("/api/sessions/import", json=data).status_code == code
    assert len(client.get("/api/sessions").json()) == before


def test_a_folded_count_past_the_end_covers_what_is_there(client, lived_in):
    data = export(client, lived_in)
    data["folded"] = 10_000
    new = bring_back(client, data)["id"]
    with SessionLocal() as db:
        s = db.get(ChatSession, new)
        assert s.summarized_upto_id == s.messages[-1].id


def test_an_avatar_that_is_not_a_png_is_dropped(client, lived_in):
    data = export(client, lived_in)
    for p in data["characters"]:
        p["avatar"] = base64.b64encode(b"<script>not a picture</script>").decode()
    wipe()
    new = bring_back(client, data)["id"]
    with SessionLocal() as db:
        assert db.get(ChatSession, new).character.avatar_path is None


def test_a_damaged_card_is_refused_and_writes_nothing(client, lived_in):
    data = export(client, lived_in)
    wipe()
    for p in data["characters"]:
        p["card"]["tags"] = "not a list"
    assert client.post("/api/sessions/import", json=data).status_code == 400
    assert client.get("/api/sessions").json() == [] and client.get("/api/characters").json() == []


def test_a_rhythm_the_scheduler_cannot_read_is_dropped(client, lived_in):
    data = export(client, lived_in)
    data["characters"][0]["rhythm"] = {"wake": "soon", "sleep": "23:00", "busy_from": "08:00", "busy_until": "17:00",
                                       "reach": "constantly"}
    wipe()
    new = bring_back(client, data)["id"]
    assert client.get(f"/api/sessions/{new}").status_code == 200
    with SessionLocal() as db:
        assert db.get(ChatSession, new).character.rhythm == {"busy_from": "08:00", "busy_until": "17:00"}


def test_export_of_a_missing_chat(client):
    assert client.get("/api/sessions/999/export").status_code == 404


def test_the_download_has_a_safe_filename(client, lived_in):
    client.patch(f"/api/sessions/{lived_in}", json={"title": 'A "quoted"/odd\ntitle'})
    r = client.get(f"/api/sessions/{lived_in}/export")
    disposition = r.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="') and "/" not in disposition.split("=", 1)[1]
    assert "\n" not in disposition


def test_format_constant_matches_the_file(client, lived_in):
    assert export(client, lived_in)["format"] == transfer.FORMAT
