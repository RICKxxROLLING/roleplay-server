"""Settings → Safety: what the owner can choose for adults, and what never changes for minors."""
from __future__ import annotations

import pytest
from conftest import sse_events

from app.cards.models import CharacterCard
from app.memory import trust


def card(description: str, tags: list[str] | None = None) -> CharacterCard:
    return CharacterCard(name="Sam", description=description, tags=tags or [])


ADULT = "Sam is a 24-year-old barista."
NO_AGE = "Sam keeps the café and paints on weekends."
MINOR = "Sam is a 15-year-old student."


@pytest.mark.parametrize("text, age", [
    ("Sadie Wren is a 24-year-old barista.", 24), ("Hana, 54, keeps the store.", 54),
    ("A woman aged 30 who fixes boats.", 30), (NO_AGE, None), (MINOR, None),
])
def test_stated_ages(text, age):
    assert trust.stated_age(card(text)) == age


def test_the_default_is_romance_only_for_tagged_cards(client):
    assert not trust.romance_allowed(card(ADULT))
    assert trust.romance_allowed(card(ADULT, ["romance"]))
    assert not trust.romance_allowed(card(MINOR, ["romance"]))


@pytest.mark.parametrize("mode, untagged, tagged", [("adults", True, True), ("never", False, False)])
def test_romance_for_any_adult_or_nobody(client, mode, untagged, tagged):
    client.patch("/api/settings", json={"romance_mode": mode})
    assert trust.romance_allowed(card(ADULT)) is untagged
    assert trust.romance_allowed(card(ADULT, ["romance"])) is tagged
    # Never for a minor, whatever the setting.
    assert not trust.romance_allowed(card(MINOR, ["romance"]))
    assert not trust.romance_allowed(card(MINOR))


def test_a_card_with_no_age_can_be_kept_platonic(client):
    assert trust.romance_allowed(card(NO_AGE, ["romance"])) and not trust.kept_platonic(card(NO_AGE))
    client.patch("/api/settings", json={"unknown_age": "platonic"})
    assert not trust.romance_allowed(card(NO_AGE, ["romance"])) and trust.kept_platonic(card(NO_AGE))
    assert trust.romance_allowed(card(ADULT, ["romance"]))  # a stated adult age is unaffected


def test_bad_values_are_refused(client):
    assert client.patch("/api/settings", json={"romance_mode": "everyone"}).status_code == 422
    assert client.patch("/api/settings", json={"unknown_age": "child"}).status_code == 422


def test_the_platonic_line_reaches_the_prompt_even_with_trust_off(client, llm):
    client.patch("/api/settings", json={"romance_mode": "never", "trust_enabled": False})
    cid = client.post("/api/characters", json={"name": "Sam", "description": ADULT, "tags": ["romance"]}).json()["id"]
    sid = client.post("/api/sessions", json={"character_id": cid}).json()["id"]
    sse_events(client.post(f"/api/sessions/{sid}/messages", json={"content": "Hello."}))
    assert any("stays platonic" in p and "kindly says no" in p for p in llm.prompts)


def test_no_pictures_of_a_card_with_no_age_when_kept_platonic(client):
    client.patch("/api/settings", json={"unknown_age": "platonic"})
    cid = client.post("/api/characters", json={"name": "Sam", "description": NO_AGE}).json()["id"]
    r = client.post(f"/api/characters/{cid}/portrait")
    assert r.status_code == 400 and "no age" in r.json()["detail"]


def test_the_people_list_and_the_romance_switch(client):
    adult = client.post("/api/characters", json={"name": "Ana", "description": ADULT}).json()["id"]
    young = client.post("/api/characters", json={"name": "Kit", "description": MINOR}).json()["id"]
    rows = {p["name"]: p for p in client.get("/api/safety/people").json()}
    assert rows["Ana"]["age"] == 24 and not rows["Ana"]["romance"] and rows["Ana"]["pictured"]
    assert rows["Kit"]["minor"] and not rows["Kit"]["pictured"]
    got = client.put(f"/api/safety/people/{adult}/romance", json={"tagged": True}).json()
    assert got["romance_tag"] and got["romance"]
    assert "romance" in client.get(f"/api/characters/{adult}").json()["card"]["tags"]
    assert client.put(f"/api/safety/people/{young}/romance", json={"tagged": True}).status_code == 400
    got = client.put(f"/api/safety/people/{adult}/romance", json={"tagged": False}).json()
    assert not got["romance_tag"]


# --- Explicit content: adults only, opt-in --------------------------------------------------

def test_explicit_writing_needs_the_switch_romance_and_a_stated_adult_age(client):
    adult = card(ADULT, ["romance"])
    assert not trust.explicit_text_allowed(adult)  # off by default
    client.patch("/api/settings", json={"explicit_text": True})
    assert trust.explicit_text_allowed(adult)
    assert not trust.explicit_text_allowed(card(ADULT))  # no romance for this card
    assert not trust.explicit_text_allowed(card(NO_AGE, ["romance"]))  # no stated age
    assert not trust.explicit_text_allowed(card(MINOR, ["romance"]))
    client.patch("/api/settings", json={"romance_mode": "adults"})
    assert not trust.explicit_text_allowed(card(MINOR))  # never, whatever the setting


def test_the_explicit_line_reaches_the_prompt_only_when_allowed(client, llm):
    client.patch("/api/settings", json={"explicit_text": True})
    cid = client.post("/api/characters", json={"name": "Sam", "description": ADULT, "tags": ["romance"]}).json()["id"]
    young = client.post("/api/characters", json={"name": "Kit", "description": MINOR, "tags": ["romance"]}).json()["id"]
    for char, expect in ((cid, True), (young, False)):
        llm.prompts.clear()
        sid = client.post("/api/sessions", json={"character_id": char}).json()["id"]
        sse_events(client.post(f"/api/sessions/{sid}/messages", json={"content": "Hello."}))
        assert any("may be written explicitly" in p for p in llm.prompts) is expect


def test_a_persona_written_as_a_minor_turns_explicit_writing_off(client, llm):
    client.patch("/api/settings", json={"explicit_text": True})
    pid = client.post("/api/personas", json={"name": "Jo", "description": "Jo is a 16-year-old student."}).json()["id"]
    cid = client.post("/api/characters", json={"name": "Sam", "description": ADULT, "tags": ["romance"]}).json()["id"]
    sid = client.post("/api/sessions", json={"character_id": cid, "persona_id": pid}).json()["id"]
    sse_events(client.post(f"/api/sessions/{sid}/messages", json={"content": "Hello."}))
    assert not any("may be written explicitly" in p for p in llm.prompts)


def test_explicit_pictures_only_for_qualifying_adults(client):
    from types import SimpleNamespace as NS

    from app import images

    adult = NS(name="Sam", card={"name": "Sam", "description": ADULT, "tags": ["romance"]})
    untagged = NS(name="Ana", card={"name": "Ana", "description": ADULT})
    minor = NS(name="Kit", card={"name": "Kit", "description": MINOR, "tags": ["romance"]})
    assert not images.explicit_ok([adult])  # off by default
    client.patch("/api/settings", json={"explicit_pictures": True})
    assert images.explicit_ok([adult])
    assert not images.explicit_ok([adult, untagged]) and not images.explicit_ok([adult, minor])
    assert not images.explicit_ok([adult], "Jo is a 15-year-old.")
    rows = {p["name"]: p for p in client.get("/api/safety/people").json()}  # list still loads
    assert isinstance(rows, dict)
