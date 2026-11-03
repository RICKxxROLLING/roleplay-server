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
    chat(client, session)  # the scene before this reply comes from the greeting
    client.patch(f"/api/sessions/{session}/scene", json={"location": "the roof"})
    sse_events(client.post(f"/api/sessions/{session}/regenerate"))
    previous = llm.scene_calls[-1].split("PREVIOUS SCENE")[1]
    assert "the flooded archive" in previous
    assert "the roof" not in previous


def test_location_changes_only_when_someone_moves():
    from app.memory.scene import moved

    assert moved("*I walk over to the stables.*", "He nods.")
    assert moved("Hi.", "*She steps outside into the snow.*")
    assert moved("Hi.", "*The next morning she is up early.*")
    # Mentioning a place, or talking about going somewhere, moves nobody.
    assert not moved("Tell me about the stables.", '*She smiles.* "I walked there once, and the lookout tower too."')
    assert not moved("*I top up your cocoa.*", "*She takes the mug by the stove.*")


def test_a_mentioned_place_does_not_replace_the_location(client, session, llm):
    chat(client, session)
    llm.scene_reply = " the lookout tower\nTime: night\nWeather: indoors\nPresent: Seraphine, You"
    llm.replies = ['*She sips her cocoa.* "That lookout tower needs better lights."']
    sse_events(client.post(f"/api/sessions/{session}/messages", json={"content": "*I watch the stove.*"}))
    scene = client.get(f"/api/sessions/{session}").json()["scene"]
    assert scene["location"] == "the flooded archive" and scene["time"] == "night"
    llm.replies = ["*She walks to the lookout tower.*"]
    sse_events(client.post(f"/api/sessions/{session}/messages", json={"content": "Hm."}))
    assert client.get(f"/api/sessions/{session}").json()["scene"]["location"] == "the lookout tower"


def test_replies_cut_by_the_cap_end_on_a_whole_sentence():
    from app.chat.orchestrator import _whole_sentences

    assert _whole_sentences("She smiled. Then she looked at the") == "She smiled."
    assert _whole_sentences("*She smiled. Then she looked at the") == "*She smiled.*"
    assert _whole_sentences('"Well. I never really thought') == '"Well."'
    assert _whole_sentences("All done.") == "All done."
    assert _whole_sentences("no sentence end here") == "no sentence end here"


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
