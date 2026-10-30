"""Character creator: drafting a card from an idea, or fleshing out a thin one."""
from __future__ import annotations

from conftest import sse_events

from app.cards.creator import parse_draft


def draft(client, **body):
    events = sse_events(client.post("/api/characters/draft", json=body))
    return next(e for e in events if e["type"] == "draft"), events


def test_a_draft_fills_every_field(client, llm):
    d, events = draft(client, brief="A tavern keeper who remembers every debt")
    f = d["fields"]
    assert f["name"] == "Bram Halloway"
    assert "brass ledger" in f["description"]
    assert f["personality"].startswith("Gruff")
    assert f["first_mes"].startswith("*Rain hammers")
    assert f["mes_example"].startswith("<START>") and "<END>" not in f["mes_example"]
    assert d["missing"] == [] and d["name_taken"] is False
    assert any(e["type"] == "token" for e in events)
    assert "A tavern keeper who remembers every debt" in llm.draft_calls[0]


def test_drafting_saves_nothing(client, llm):
    before = client.get("/api/characters").json()
    draft(client, brief="A tavern keeper")
    assert client.get("/api/characters").json() == before


def test_a_taken_name_is_flagged(client, llm):
    client.post("/api/characters", json={"name": "Bram Halloway"})
    d, _ = draft(client, brief="A tavern keeper")
    assert d["name_taken"] is True


def test_a_world_sets_the_scene(client, llm):
    w = client.post("/api/worlds", json={
        "name": "Jackson", "description": "A walled settlement in Wyoming.",
        "entries": [{"keys": ["stables"], "content": "Forty horses.", "name": "Stables"}]}).json()
    tommy = client.post("/api/characters", json={"name": "Tommy"}).json()
    client.put(f"/api/worlds/membership/{tommy['id']}", json={"world_id": w["id"]})
    draft(client, brief="A medic", world_id=w["id"])
    p = llm.draft_calls[0]
    assert "It is set in Jackson: A walled settlement in Wyoming." in p
    assert "Known in this world: Stables." in p
    assert "Tommy" in p and "{{user}} is none of them" in p


def test_expanding_keeps_the_existing_card_in_view(client, llm):
    laura = client.post("/api/characters", json={
        "name": "Laura Hart", "description": "{{char}} is a veteran voice actress."}).json()
    d, _ = draft(client, character_id=laura["id"])
    p = llm.draft_calls[0]
    assert "Keep every name, fact and detail" in p
    assert "{{char}} is a veteran voice actress." in p
    assert p.rstrip().endswith("Name: Laura Hart\nDescription:")
    assert d["name_taken"] is False  # her own name is not a clash


def test_an_empty_brief_is_refused(client, llm):
    assert client.post("/api/characters/draft", json={"brief": "  "}).status_code == 400


# --- parsing what an 8B actually writes ---------------------------------------

def test_a_short_or_runaway_section_is_dropped_alone():
    d = parse_draft(" Mira\nDescription: Too short.\nPersonality: Calm.\n"
                    "First message: " + "x" * 120)
    assert d.fields["name"] == "Mira" and "description" not in d.fields
    assert "Description" in d.missing and d.fields["personality"] == "Calm."


def test_example_dialogue_written_straight_after_the_greeting_is_split_off():
    d = parse_draft(" Kira\nFirst message: *Kira sets down her cloth.* \"Rough day?\" " + "y" * 80
                    + "\n\n<START>\n{{user}}: Heavy losses.\n{{char}}: \"Relentless?\"")
    assert "<START>" not in d.fields["first_mes"]
    assert d.fields["mes_example"].startswith("<START>\n{{user}}: Heavy losses.")


def test_a_dropped_opening_asterisk_is_restored():
    d = parse_draft(" Laura\nFirst message: As the door chimes, Laura rises from her chair.* "
                    "\"Ah, perfect timing!\" she calls out cheerfully, waving you in from the cold.")
    assert d.fields["first_mes"].startswith("*As the door chimes")


def test_labels_in_bold_or_any_case_are_read():
    d = parse_draft(" Ash\n**Personality:** Wry.\nfirst message: " + "z" * 90)
    assert d.fields["personality"] == "Wry." and d.fields["first_mes"].startswith("z")
