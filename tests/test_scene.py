"""Scene state: updated after each reply, handed back before the next."""
from __future__ import annotations

from app.memory.scene import parse_scene
from conftest import sse_events


def chat(client, session, text="hi"):
    return sse_events(client.post(f"/api/sessions/{session}/messages", json={"content": text}))


def test_a_reply_updates_the_scene(client, session, llm):
    events = chat(client, session)
    scene = {"location": "the flooded archive", "time": "midnight",
             "weather": "indoors", "present": "Seraphine, You"}
    assert {"type": "scene", "scene": scene} in events
    assert client.get(f"/api/sessions/{session}").json()["scene"] == scene


def test_the_next_prompt_carries_the_scene_near_the_end(client, session, llm):
    chat(client, session)
    prompt = client.get(f"/api/sessions/{session}/prompt").json()["prompt"]
    line = "Current scene -- Location: the flooded archive; Time: midnight"
    assert line in prompt
    # In the tail, after the history, so it is the freshest thing the model reads.
    assert prompt.index(line) > prompt.index("reply 1")


def test_scene_can_be_turned_off(client, session, llm):
    client.patch("/api/settings", json={"scene_enabled": False})
    events = chat(client, session)
    assert llm.scene_calls == []
    assert not any(e["type"] == "scene" for e in events)
    assert "Current scene" not in client.get(f"/api/sessions/{session}/prompt").json()["prompt"]


def test_a_misfire_keeps_the_old_scene(client, session, llm):
    chat(client, session)
    llm.scene_reply = ' *She looks around the room.* "Where are we?"'
    chat(client, session, "again")
    assert client.get(f"/api/sessions/{session}").json()["scene"]["location"] == "the flooded archive"


def test_a_swipe_starts_from_the_scene_before_the_reply_it_replaces(client, session, llm):
    chat(client, session)  # scene moves from blank to the archive
    sse_events(client.post(f"/api/sessions/{session}/regenerate"))
    assert "Location: unknown" in llm.scene_calls[-1]
    assert "the flooded archive" not in llm.scene_calls[-1].split("LATEST EXCHANGE")[0]


def test_the_scene_is_editable(client, session, llm):
    chat(client, session)
    r = client.patch(f"/api/sessions/{session}/scene", json={"weather": "heavy rain"})
    assert r.json()["scene"]["weather"] == "heavy rain"
    assert r.json()["scene"]["location"] == "the flooded archive"
    prompt = client.get(f"/api/sessions/{session}/prompt").json()["prompt"]
    assert "Weather: heavy rain" in prompt


def test_parse_needs_all_four_short_fields():
    ok = parse_scene(" the porch\nTime: night\nWeather: light snow\nPresent: Dina, Riley")
    assert ok == {"location": "the porch", "time": "night",
                  "weather": "light snow", "present": "Dina, Riley"}
    assert parse_scene(" the porch\nTime: night") is None
    assert parse_scene(" the porch\nTime: night\nWeather: " + "x" * 120 + "\nPresent: Dina") is None
