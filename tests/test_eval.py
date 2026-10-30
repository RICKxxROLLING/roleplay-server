"""Test this model: the scripted chat runs through the real pipeline and cleans up."""
from __future__ import annotations

from conftest import sse_events


def run(client, imported, **kw):
    return sse_events(client.post("/api/eval", json={"character_id": imported, **kw}))


def test_eval_streams_progress_then_a_result(client, imported, llm):
    events = run(client, imported, turns=3)
    assert [e["type"] for e in events] == ["progress", "progress", "progress", "result"]
    summary = events[-1]["summary"]
    assert summary["turns"] == 3
    assert summary["empty_replies"] == 0
    assert summary["avg_words"] > 0


def test_eval_leaves_no_session_behind(client, imported, llm):
    before = len(client.get("/api/sessions").json())
    run(client, imported, turns=2)
    assert len(client.get("/api/sessions").json()) == before


def test_eval_goes_through_the_guard(client, imported, llm):
    llm.reply = "*You nod slowly. She smiles and pours the tea for both of them.*"
    summary = run(client, imported, turns=2)[-1]["summary"]
    assert summary["acted_for_you"] == 2
    assert summary["lines_removed"] == 2


def test_eval_flags_first_person_narration(client, imported, llm):
    llm.reply = "*I nod. I smile at my guest. I mean it.*"
    summary = run(client, imported, turns=3)[-1]["summary"]
    assert summary["first_person_saved"] == 3
    assert any("first person" in n for n in summary["health"])


def test_eval_rejects_an_unknown_character(client, llm):
    events = sse_events(client.post("/api/eval", json={"character_id": 999}))
    assert events[-1]["type"] == "error"
