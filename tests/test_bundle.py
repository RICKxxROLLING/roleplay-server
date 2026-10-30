"""Importing a bundle of worlds and characters from the UI."""
from __future__ import annotations

BUNDLE = {
    "format": "roleplay-bundle",
    "version": 1,
    "worlds": [{"name": "Harbor Town", "description": "A fishing town behind a sea wall.",
                "entries": [{"keys": ["lighthouse"], "content": "The lighthouse is dark.", "name": "Lighthouse"}]}],
    "characters": [
        {"world": "Harbor Town", "card": {"spec": "chara_card_v2", "data": {
            "name": "Mara", "description": "{{char}} mends nets on the pier.", "first_mes": "Hey."}}},
        {"world": None, "card": {"name": "Wren", "description": "A drifter.", "first_mes": "Hi."}},
    ],
}


def imp(client, bundle=BUNDLE, replace=False):
    r = client.post("/api/import/bundle", json={**bundle, "replace": replace})
    assert r.status_code == 200, r.text
    return r.json()


def by_name(client):
    return {c["name"]: c for c in client.get("/api/characters").json()}


def test_creates_worlds_and_characters_and_links_them(client):
    r = imp(client)
    assert r["worlds_created"] == ["Harbor Town"] and r["characters_created"] == ["Mara", "Wren"]
    chars = by_name(client)
    world = client.get("/api/worlds").json()[0]
    assert chars["Mara"]["world_id"] == world["id"] and chars["Wren"]["world_id"] is None
    assert world["entries"][0]["content"] == "The lighthouse is dark."


def test_importing_again_skips_existing_characters(client):
    imp(client)
    r = imp(client)
    assert r["characters_skipped"] == ["Mara", "Wren"] and r["worlds_updated"] == ["Harbor Town"]
    assert len(client.get("/api/characters").json()) == 2


def test_replace_updates_the_card_and_keeps_the_chats(client):
    imp(client)
    mara = by_name(client)["Mara"]["id"]
    sid = client.post("/api/sessions", json={"character_id": mara}).json()["id"]
    changed = {**BUNDLE, "characters": [{"world": "Harbor Town", "card": {
        "name": "mara", "description": "{{char}} keeps the lighthouse now.", "first_mes": "Hey."}}]}
    r = imp(client, changed, replace=True)
    assert r["characters_updated"] == ["Mara"]
    card = client.get(f"/api/characters/{mara}").json()["card"]
    assert "keeps the lighthouse" in card["description"]
    assert client.get(f"/api/sessions/{sid}").status_code == 200  # the chat is still there


def test_an_unknown_world_is_refused(client):
    bad = {**BUNDLE, "worlds": [], "characters": [{"world": "Nowhere", "card": {"name": "X"}}]}
    assert client.post("/api/import/bundle", json=bad).status_code == 400


def test_the_wrong_format_is_refused(client):
    assert client.post("/api/import/bundle", json={**BUNDLE, "format": "other"}).status_code == 400

