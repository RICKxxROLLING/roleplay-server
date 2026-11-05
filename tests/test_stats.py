"""The Stats panel's numbers come from what's stored, and nothing private to the host shows."""
from __future__ import annotations

import datetime as dt

from conftest import sse_events

from app.routes.stats import _streaks


def say(client, sid, text):
    return sse_events(client.post(f"/api/sessions/{sid}/messages", json={"content": text}))


def test_an_empty_install_has_zeroes_not_errors(client):
    r = client.get("/api/stats?tz=Europe/London")
    assert r.status_code == 200
    got = r.json()
    assert got["tiles"]["chats"] == 0 and got["tiles"]["your_words"] == 0
    assert len(got["by_day"]) == 60 and len(got["by_hour"]) == 24 and len(got["by_weekday"]) == 7
    assert got["longest_reply"] is None and got["most_swiped"] is None


def test_counts_follow_the_chat(client, session, llm):
    say(client, session, "The lighthouse keeper hides a lantern.")
    say(client, session, "Tell me about the lantern again.")
    sse_events(client.post(f"/api/sessions/{session}/regenerate", json={}))
    got = client.get("/api/stats").json()
    t = got["tiles"]
    assert t["chats"] == 1 and t["your_messages"] == 2
    assert t["your_words"] == 12
    assert got["top_characters"][0] == {"label": "Seraphine", "value": 3}
    assert {"label": "lantern", "value": 2} in got["your_words_most_used"]
    assert t["swipes"] == 1 and got["most_swiped"]["name"] == "Seraphine"
    assert sum(d["value"] for d in got["by_day"]) == 2
    assert got["tics"] == [] or all(r["value"] > 0 for r in got["tics"])


def test_a_bad_time_zone_falls_back(client):
    assert client.get("/api/stats?tz=Not/AZone").status_code == 200


def test_world_chats_show_places_and_people_but_not_the_host(client, llm):
    w = client.post("/api/worlds", json={"name": "Jackson"}).json()
    cid = client.post("/api/characters", json={"name": "Rosa", "description": "Rosa keeps the greenhouse."}).json()["id"]
    client.put(f"/api/worlds/membership/{cid}", json={"world_id": w["id"]})
    sid = client.post("/api/sessions/world", json={"world_id": w["id"], "place": "the greenhouse"}).json()["id"]
    sse_events(client.post(f"/api/sessions/{sid}/respond", json={}))
    got = client.get("/api/stats").json()
    assert got["tiles"]["world_chats"] == 1 and got["tiles"]["people_met"] == 1
    assert got["meetings_by_world"] == [{"label": "Jackson", "value": 1}]
    assert got["places"] == [{"label": "The greenhouse", "value": 1}]
    assert got["tiles"]["characters"] == 1
    assert all(r["label"] != "(world chat)" for r in got["top_characters"])


def test_streaks():
    today = dt.date.today()
    days = {today, today - dt.timedelta(days=1), today - dt.timedelta(days=5),
            today - dt.timedelta(days=6), today - dt.timedelta(days=7)}
    assert _streaks(days) == (3, 2)
    assert _streaks(set()) == (0, 0)
    assert _streaks({today - dt.timedelta(days=3)}) == (1, 0)
