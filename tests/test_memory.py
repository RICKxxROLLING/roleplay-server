"""Rolling summarization: watermark behaviour and fold safety (Phase 2)."""
from __future__ import annotations

from app.db import ChatSession, Message, SessionLocal
from conftest import sse_events


def flood(client, session, turns=12, size=6):
    """Drive enough turns to cross the fold threshold."""
    events = []
    for i in range(turns):
        r = client.post(
            f"/api/sessions/{session}/messages",
            json={"content": f"Turn {i}: " + "we searched the lower stacks. " * size},
        )
        events.extend(e for e in sse_events(r) if e["type"] == "memory")
    return events


def test_no_summary_early(client, session):
    m = client.get(f"/api/sessions/{session}/memory").json()
    assert m["summarized_count"] == 0
    assert m["will_summarize_next_turn"] is False


def test_fold_fires_and_advances_watermark(client, session, llm):
    events = flood(client, session)
    assert any(e["status"] == "compressing" for e in events)
    assert any(e["status"] == "done" for e in events)

    m = client.get(f"/api/sessions/{session}/memory").json()
    assert m["summarized_count"] > 0
    assert m["summarized_upto_id"] > 0
    assert m["summary"].startswith("FOLD#")


def test_folded_messages_leave_the_prompt(client, session, llm):
    flood(client, session)
    prompt = client.get(f"/api/sessions/{session}/prompt").json()["prompt"]

    db = SessionLocal()
    s = db.get(ChatSession, session)
    folded = [m for m in s.messages if m.id <= s.summarized_upto_id]
    pending = [m for m in s.messages if m.id > s.summarized_upto_id]
    db.close()

    assert folded, "nothing was folded"
    for m in folded:
        marker = m.content[:24]
        if marker.startswith("Turn "):
            assert marker not in prompt
    for m in pending:
        if m.content.startswith("Turn "):
            assert m.content[:24] in prompt
    assert "Story so far:" in prompt


def test_summary_stays_bounded_across_folds(client, session, llm):
    flood(client, session, turns=20)
    assert len(llm.summary_calls) >= 2, "expected multiple folds"
    sizes = [len(p) for p in llm.summary_calls]
    # A fold rewrites the summary; an append-only log would grow without limit.
    assert max(sizes) < 8000


def test_failed_fold_does_not_advance_watermark(client, session, llm, monkeypatch):
    """Losing turns into a black hole is far worse than skipping a fold."""
    flood(client, session)
    db = SessionLocal()
    before = db.get(ChatSession, session).summarized_upto_id
    db.close()

    async def useless(prompt, params):
        return "x"  # too short; summarizer rejects it

    import app.memory.summarizer as summarizer

    monkeypatch.setattr(summarizer.get_client(), "generate", useless)
    client.post(f"/api/sessions/{session}/summarize")

    db = SessionLocal()
    after = db.get(ChatSession, session).summarized_upto_id
    db.close()
    assert before == after


def test_summary_is_hand_editable(client, session, llm):
    flood(client, session)
    client.patch(f"/api/sessions/{session}/memory", json={"summary": "Hand written."})
    prompt = client.get(f"/api/sessions/{session}/prompt").json()["prompt"]
    assert "Hand written." in prompt


def test_editing_below_watermark_is_flagged(client, session, llm):
    """The edit is allowed but can't reach the model -- say so."""
    flood(client, session)
    db = SessionLocal()
    s = db.get(ChatSession, session)
    old = [m.id for m in s.messages if m.id <= s.summarized_upto_id][0]
    db.close()

    r = client.patch(
        f"/api/sessions/{session}/messages/{old}", json={"content": "changed"}
    ).json()
    assert r["below_watermark"] is True
    assert r["note"]


def test_summarization_can_be_disabled(client, session, llm):
    client.patch("/api/settings", json={"summary_enabled": False})
    flood(client, session)
    assert client.get(f"/api/sessions/{session}/memory").json()["summarized_count"] == 0


# --- summaries must not turn back into roleplay ---------------------------


def test_a_roleplay_summary_is_rejected(client, session, llm, monkeypatch):
    """Observed live: a fold came back as stage directions and dialogue, which
    then got injected as "Story so far" on every later turn.

    Worse, it is self-perpetuating -- the next fold is handed that summary and
    told to rewrite it, so dialogue begets dialogue. Never storing one is what
    stops the loop starting."""
    flood(client, session)
    db = SessionLocal()
    before = db.get(ChatSession, session).summary
    db.close()
    assert before.startswith("FOLD#"), "setup: expected a normal summary first"

    async def in_character(prompt, params):
        return (
            'Elizabeth: *She looks up with relief in her eyes.* "Without your help '
            'I could not have done it." *She takes his hand.* "Thank you."'
        )

    import app.memory.summarizer as summarizer

    monkeypatch.setattr(summarizer.get_client(), "generate", in_character)
    client.post(f"/api/sessions/{session}/summarize")

    db = SessionLocal()
    after = db.get(ChatSession, session).summary
    db.close()
    assert after == before, "roleplay output must not replace a good summary"


def test_rejecting_a_summary_leaves_the_watermark(client, session, llm, monkeypatch):
    """Retrying is the recovery path: the same turns get folded again next time
    against a longer transcript, which is what pulls the model back to
    summarising."""
    flood(client, session)
    db = SessionLocal()
    before = db.get(ChatSession, session).summarized_upto_id
    db.close()

    async def in_character(prompt, params):
        return '*She nods once.* "As you wish," she whispered, taking his hand gently.'

    import app.memory.summarizer as summarizer

    monkeypatch.setattr(summarizer.get_client(), "generate", in_character)
    client.post(f"/api/sessions/{session}/summarize")

    db = SessionLocal()
    after = db.get(ChatSession, session).summarized_upto_id
    db.close()
    assert before == after


def test_speaker_label_is_stripped_before_judging():
    """A stray "Name:" prefix alone shouldn't discard an otherwise good summary."""
    from app.memory.summarizer import _clean, looks_like_roleplay

    text = "Elizabeth Rockwell: She travelled north with him and reached Aldermere."
    cleaned = _clean(text)
    assert cleaned.startswith("She travelled north")
    assert not looks_like_roleplay(cleaned)


def test_prose_summaries_are_accepted():
    from app.memory.summarizer import looks_like_roleplay

    assert not looks_like_roleplay(
        "Elizabeth gathered evidence that the manuscript was a forgery and agreed "
        "to present it at the hearing. Riley waited by the river."
    )
