"""Shared worlds: lore and a setting reused by every character set there."""
from __future__ import annotations

from conftest import sse_events

JACKSON = {
    "name": "Jackson",
    "description": "A fortified settlement in Wyoming, walled against the infected.",
    "entries": [
        {"keys": ["stables"], "content": "The Jackson stables hold forty horses.", "name": "Stables"},
        {"keys": ["gate"], "content": "The north gate is manned day and night.", "name": "Gate"},
    ],
}


def make_world(client, **over):
    r = client.post("/api/worlds", json={**JACKSON, **over})
    assert r.status_code == 200, r.text
    return r.json()


def join(client, char_id, world_id):
    return client.put(f"/api/worlds/membership/{char_id}", json={"world_id": world_id})


def prompt(client, session):
    return client.get(f"/api/sessions/{session}/prompt").json()


def test_world_lore_fires_only_when_mentioned(client, imported, session, llm):
    w = make_world(client)
    join(client, imported, w["id"])
    client.post(f"/api/sessions/{session}/messages", json={"content": "Shall we visit the stables?"})
    p = prompt(client, session)
    assert "forty horses" in p["prompt"]
    assert "north gate" not in p["prompt"]
    assert "Stables" in p["lore_fired"]


def test_the_world_description_is_the_setting(client, imported, session, llm):
    w = make_world(client)
    join(client, imported, w["id"])
    text = prompt(client, session)["prompt"]
    assert "Setting: A fortified settlement in Wyoming" in text
    assert text.index("Setting:") < text.index("is a wandering archivist")  # above the character block


def test_two_characters_share_one_world(client, imported, llm):
    w = make_world(client)
    other = client.post("/api/characters", json={"name": "Tommy", "first_mes": "Hey."}).json()
    join(client, imported, w["id"])
    join(client, other["id"], w["id"])
    got = client.get(f"/api/worlds/{w['id']}").json()
    assert {c["name"] for c in got["characters"]} == {"Seraphine", "Tommy"}
    # One edit reaches both.
    client.patch(f"/api/worlds/{w['id']}", json={"description": "A walled town by the river."})
    for cid in (imported, other["id"]):
        s = client.post("/api/sessions", json={"character_id": cid}).json()["id"]
        assert "Setting: A walled town by the river." in prompt(client, s)["prompt"]


def test_leaving_a_world_drops_its_lore(client, imported, session, llm):
    w = make_world(client)
    join(client, imported, w["id"])
    join(client, imported, None)
    assert "Setting:" not in prompt(client, session)["prompt"]


def test_deleting_a_world_keeps_its_characters(client, imported, llm):
    w = make_world(client)
    join(client, imported, w["id"])
    assert client.delete(f"/api/worlds/{w['id']}").status_code == 200
    c = client.get(f"/api/characters/{imported}").json()
    assert c["world_id"] is None


def test_world_entries_are_validated_like_card_entries(client, llm):
    r = client.post("/api/worlds", json={"name": "X", "entries": [{"keys": "not-a-list"}]})
    assert r.status_code == 422
    assert client.post("/api/worlds", json={"name": "  "}).status_code == 400
