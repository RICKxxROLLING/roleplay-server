"""Vector retrieval over past turns (Phase 4).

The scenario these are built around is the one the whole phase exists for:
a concrete fact stated early, summarised away, and then asked about later.
Everything else here is a guard on the ways that can go quietly wrong --
retrieving something already in the prompt, retrieving noise, or letting a dead
embedding backend cost the user their turn.
"""
from __future__ import annotations

import pytest

from app.db import ChatSession, MessageEmbedding, SessionLocal
from app.memory import rag
from app.prompt import estimate_tokens
from conftest import enable_retrieval, sse_events

FACT = "The innkeeper introduced himself as Bram Halloway."
QUERY = "Who was the innkeeper called, Bram?"
# Deliberately shares no vocabulary with FACT or QUERY, so a filler turn scoring
# above the target would be a real ranking failure rather than word overlap.
FILLER = "Rain hammered against shuttered windows all night. " * 6


def chat(client, session, text):
    r = client.post(f"/api/sessions/{session}/messages", json={"content": text})
    assert r.status_code == 200, r.text
    return sse_events(r)


def last_turn_prompt(llm) -> str:
    """The most recent prompt that wasn't a summarisation call."""
    return next(p for p in reversed(llm.prompts) if "memory log" not in p.lower())


def bury(client, session, turns=6):
    """Drive enough filler turns to push the opening exchange below the watermark."""
    for _ in range(turns):
        chat(client, session, FILLER)


def message_ids(session_id):
    db = SessionLocal()
    try:
        s = db.get(ChatSession, session_id)
        return [(m.id, m.content) for m in s.messages], s.summarized_upto_id
    finally:
        db.close()


# --- vector plumbing -------------------------------------------------------


def test_vectors_are_normalised_on_write():
    """Cosine is only a dot product if both sides are unit length."""
    v = rag.unpack(rag.pack([3.0, 4.0, 0.0]))
    assert rag.similarity(v, v) == pytest.approx(1.0, abs=1e-5)
    assert list(v) == pytest.approx([0.6, 0.8, 0.0], abs=1e-6)


def test_zero_vector_does_not_divide_by_zero():
    v = rag.unpack(rag.pack([0.0, 0.0, 0.0]))
    assert rag.similarity(v, v) == 0.0


def test_similarity_of_different_lengths_is_zero():
    """Vectors from two different embedding models must never be compared."""
    a = rag.unpack(rag.pack([1.0, 0.0]))
    b = rag.unpack(rag.pack([1.0, 0.0, 0.0]))
    assert rag.similarity(a, b) == 0.0


# --- indexing --------------------------------------------------------------


def test_messages_are_indexed_as_the_chat_runs(client, session, llm, embedder):
    enable_retrieval(client)
    chat(client, session, FACT)

    m = client.get(f"/api/sessions/{session}/memory").json()
    assert m["embeddable_count"] > 0
    assert m["unindexed_count"] == 0
    assert m["indexed_count"] == m["embeddable_count"]
    assert embedder.calls


def test_editing_a_message_reindexes_it(client, session, llm, embedder):
    enable_retrieval(client)
    chat(client, session, FACT)
    msgs, _ = message_ids(session)
    target = next(mid for mid, content in msgs if content == FACT)

    db = SessionLocal()
    before = db.get(MessageEmbedding, target).vector
    db.close()

    client.patch(
        f"/api/sessions/{session}/messages/{target}",
        json={"content": "The innkeeper refused to give any name at all."},
    )
    # The stored vector now describes text that is no longer there.
    assert client.get(f"/api/sessions/{session}/memory").json()["unindexed_count"] == 1

    client.post(f"/api/sessions/{session}/reindex")
    db = SessionLocal()
    after = db.get(MessageEmbedding, target).vector
    db.close()
    assert after != before


def test_changing_the_embedding_model_invalidates_the_index(
    client, session, llm, embedder
):
    """Vectors from two models share no coordinate space, so old ones are junk."""
    enable_retrieval(client)
    chat(client, session, FACT)
    assert client.get(f"/api/sessions/{session}/memory").json()["unindexed_count"] == 0

    client.patch("/api/settings", json={"embedding_model": "some-other-embedder"})
    m = client.get(f"/api/sessions/{session}/memory").json()
    assert m["unindexed_count"] == m["embeddable_count"]
    assert m["embedding_model"] == "some-other-embedder"

    r = client.post(f"/api/sessions/{session}/reindex").json()
    assert r["indexed"] == m["embeddable_count"]
    assert r["stale"] == 0


def test_reindex_backfills_a_chat_that_predates_retrieval(
    client, session, llm, embedder
):
    """Switching retrieval on mid-campaign must not orphan the history."""
    chat(client, session, FACT)
    bury(client, session, turns=2)
    assert not embedder.calls, "retrieval was off; nothing should have been embedded"

    enable_retrieval(client)
    r = client.post(f"/api/sessions/{session}/reindex").json()
    assert r["indexed"] > 0
    assert r["stale"] == 0


def test_deleting_a_message_drops_its_vector(client, session, llm, embedder):
    enable_retrieval(client)
    chat(client, session, FACT)
    msgs, _ = message_ids(session)
    target = next(mid for mid, content in msgs if content == FACT)

    client.delete(f"/api/sessions/{session}/messages/{target}")
    db = SessionLocal()
    assert db.get(MessageEmbedding, target) is None
    db.close()


def test_deleting_a_session_drops_its_vectors(client, session, llm, embedder):
    enable_retrieval(client)
    chat(client, session, FACT)
    client.delete(f"/api/sessions/{session}")

    db = SessionLocal()
    left = db.query(MessageEmbedding).filter_by(session_id=session).count()
    db.close()
    assert left == 0


# --- retrieval -------------------------------------------------------------


def test_recalls_a_fact_that_was_summarised_away(client, session, llm, embedder):
    """The headline case: precise recall of a detail the summary blurred."""
    enable_retrieval(client)
    chat(client, session, FACT)
    bury(client, session)

    msgs, watermark = message_ids(session)
    fact_id = next(mid for mid, content in msgs if content == FACT)
    assert watermark >= fact_id, "the fact was never folded; test setup is wrong"

    prompt_before = last_turn_prompt(llm)
    assert "Bram Halloway" not in prompt_before, "fact was still verbatim in context"

    chat(client, session, QUERY)
    assert "Bram Halloway" in last_turn_prompt(llm)


def test_retrieved_block_is_labelled_as_recollection(client, session, llm, embedder):
    """Out-of-order lines spliced into recent history would read as just-happened."""
    enable_retrieval(client)
    chat(client, session, FACT)
    bury(client, session)
    chat(client, session, QUERY)

    prompt = last_turn_prompt(llm)
    block = prompt.index("Relevant moments from earlier")
    assert block < prompt.index("Bram Halloway")
    # Speaker-labelled, so the model can tell who said it.
    assert f"You: {FACT}" in prompt


def test_verbatim_messages_are_not_retrieved(client, session, llm, embedder):
    """Retrieving a turn the prompt already carries buys a duplicate."""
    enable_retrieval(client)
    chat(client, session, FACT)
    chat(client, session, QUERY)

    prompt = last_turn_prompt(llm)
    assert "Relevant moments from earlier" not in prompt
    assert prompt.count(FACT) == 1


def test_low_scoring_hits_are_not_injected(client, session, llm, embedder):
    """Irrelevant retrieved context is worse than none -- it reads as plausible."""
    enable_retrieval(client, retrieval_min_score=0.99)
    chat(client, session, FACT)
    bury(client, session)
    chat(client, session, QUERY)

    assert "Relevant moments from earlier" not in last_turn_prompt(llm)


def test_unrelated_history_does_not_surface(client, session, llm, embedder):
    """A query with nothing to match must not drag in the nearest filler.

    Same setup as the recall test, same threshold, opposite question -- which is
    what makes it evidence that `retrieval_min_score` is separating signal from
    incidental word overlap rather than the fact simply always winning.
    """
    enable_retrieval(client)
    chat(client, session, FACT)
    bury(client, session)
    chat(client, session, "Describe the ceiling of the vault in detail.")

    prompt = last_turn_prompt(llm)
    assert "Bram Halloway" not in prompt
    assert "Relevant moments from earlier" not in prompt


def test_top_k_caps_the_number_of_hits(client, session, llm, embedder):
    enable_retrieval(client, retrieval_top_k=1, retrieval_min_score=0.0)
    for i in range(4):
        chat(client, session, f"{FACT} Note {i}.")
    bury(client, session)

    p = client.get(f"/api/sessions/{session}/prompt").json()
    assert len(p["retrieved"]) == 1
    assert p["retrieval_dropped"] > 0


def test_token_budget_caps_the_hits(client, session, llm, embedder):
    """Retrieval must not quietly eat the context it's meant to enrich."""
    budget = 20
    enable_retrieval(client, retrieval_top_k=10, retrieval_min_score=0.0,
                     retrieval_budget_tokens=budget)
    for i in range(4):
        chat(client, session, f"{FACT} Note {i}.")
    bury(client, session)

    p = client.get(f"/api/sessions/{session}/prompt").json()
    spent = sum(estimate_tokens(h["content"]) for h in p["retrieved"])
    assert spent <= budget
    assert p["retrieval_dropped"] > 0


def test_hits_are_injected_in_chronological_order(client, session, llm, embedder):
    """Selection ranks by score; presentation follows the story."""
    enable_retrieval(client, retrieval_top_k=5, retrieval_min_score=0.0)
    for i in range(3):
        chat(client, session, f"{FACT} Note {i}.")
    bury(client, session)

    p = client.get(f"/api/sessions/{session}/prompt").json()
    ids = [h["message_id"] for h in p["retrieved"]]
    assert ids == sorted(ids)


def test_inspector_reports_scores(client, session, llm, embedder):
    enable_retrieval(client)
    chat(client, session, FACT)
    bury(client, session)
    chat(client, session, QUERY)

    p = client.get(f"/api/sessions/{session}/prompt").json()
    assert p["retrieved_entries"] == len(p["retrieved"])
    assert p["retrieval_error"] is None
    for hit in p["retrieved"]:
        assert 0.0 <= hit["score"] <= 1.0


# --- query construction ----------------------------------------------------


def test_query_uses_only_user_turns():
    """Assistant replies are several times longer than a user turn and read
    almost identically to each other, so blending them in makes the query embed
    as generic narration. Observed live: asking for a name scored 0.81 against
    the message stating it when queried alone, and fell out of the top four
    once the surrounding prose was mixed in -- the model then invented one."""
    from app.memory.rag import build_query

    history = [
        ("user", "The innkeeper was Bram Halloway."),
        ("assistant", "*She nods, committing the name to memory.*"),
        ("user", "Take the northern road."),
        ("assistant", "*She nods, a mix of uncertainty and excitement swirling within her.*"),
        ("user", "What was the innkeeper's name?"),
    ]
    q = build_query(history)
    assert "innkeeper's name" in q
    assert "She nods" not in q, "assistant narration must not dilute the query"


def test_query_falls_back_before_the_user_has_spoken():
    """A session opens on the card's greeting; there is no user turn yet."""
    from app.memory.rag import build_query

    assert build_query([("assistant", "Hello, traveller.")]) == "Hello, traveller."


def test_query_respects_the_message_count(client):
    from app.memory.rag import build_query

    client.patch("/api/settings", json={"retrieval_query_messages": 2})
    history = [("user", "one"), ("user", "two"), ("user", "three")]
    assert build_query(history) == "two\nthree"


# --- calibration probe -----------------------------------------------------


def probe(client, session, query, **kw):
    r = client.post(f"/api/sessions/{session}/retrieve", json={"query": query, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_probe_shows_results_below_the_floor(client, session, llm, embedder):
    """The whole point: the prompt inspector only shows what survived the floor,
    so you can't see what scored just under it. A threshold picked from filtered
    data is a guess."""
    enable_retrieval(client, retrieval_min_score=0.99)
    chat(client, session, FACT)
    bury(client, session)

    p = probe(client, session, QUERY)
    assert p["results"], "nothing returned despite candidates existing"
    # Nothing can pass a 0.99 floor, but we still see the scores.
    assert all(r["would_inject"] is False for r in p["results"])
    assert any(r["rejected_by"] == "below min_score" for r in p["results"])
    assert any(r["score"] > 0 for r in p["results"])


def test_probe_marks_what_would_actually_be_injected(client, session, llm, embedder):
    enable_retrieval(client)
    chat(client, session, FACT)
    bury(client, session)

    p = probe(client, session, QUERY)
    injected = [r for r in p["results"] if r["would_inject"]]
    assert injected, "the fact should pass at the test threshold"
    assert any("Bram Halloway" in r["content"] for r in injected)
    assert p["settings"]["min_score"] == 0.35


def test_probe_results_are_ranked(client, session, llm, embedder):
    enable_retrieval(client, retrieval_min_score=0.0)
    chat(client, session, FACT)
    bury(client, session)

    scores = [r["score"] for r in probe(client, session, QUERY)["results"]]
    assert scores == sorted(scores, reverse=True)


def test_probe_annotates_top_k_rejections(client, session, llm, embedder):
    """Distinguishing 'scored too low' from 'lost to top_k' is the difference
    between raising the floor and raising k."""
    enable_retrieval(client, retrieval_top_k=1, retrieval_min_score=0.0)
    for i in range(4):
        chat(client, session, f"{FACT} Note {i}.")
    bury(client, session)

    reasons = {r["rejected_by"] for r in probe(client, session, QUERY)["results"]}
    assert "over top_k" in reasons


def test_probe_excludes_what_is_already_verbatim(client, session, llm, embedder):
    """A probe that returned pending messages would misrepresent the live path."""
    enable_retrieval(client, retrieval_min_score=0.0)
    chat(client, session, FACT)

    p = probe(client, session, QUERY)
    assert all("Bram Halloway" not in r["content"] for r in p["results"])


def test_probe_does_not_generate(client, session, llm, embedder):
    enable_retrieval(client)
    chat(client, session, FACT)
    bury(client, session)
    before = llm.reply_count

    probe(client, session, QUERY)
    assert llm.reply_count == before, "probing must not cost a generation"


def test_probe_rejects_an_empty_query(client, session, llm, embedder):
    enable_retrieval(client)
    assert client.post(f"/api/sessions/{session}/retrieve", json={"query": "  "}).status_code == 400


def test_probe_reports_a_dead_embedder(client, session, llm, embedder):
    enable_retrieval(client)
    chat(client, session, FACT)
    bury(client, session)

    embedder.fail_with = RuntimeError("connection refused")
    p = probe(client, session, QUERY)
    assert p["error"] and "connection refused" in p["error"]
    assert p["results"] == []


def test_probe_works_while_retrieval_is_disabled(client, session, llm, embedder):
    """You need to see the scores *before* deciding to switch recall on."""
    enable_retrieval(client, retrieval_min_score=0.0)
    chat(client, session, FACT)
    bury(client, session)
    client.patch("/api/settings", json={"retrieval_enabled": False})

    assert probe(client, session, QUERY)["results"]


# --- failure and opt-out ---------------------------------------------------


def test_disabled_retrieval_makes_no_embedding_calls(client, session, llm, embedder):
    """Off means off: no per-turn round-trip to a backend that may not exist."""
    chat(client, session, FACT)
    chat(client, session, QUERY)
    assert embedder.calls == []


def test_a_dead_embedder_does_not_cost_the_turn(client, session, llm, embedder):
    """The reply is what the user came for; memory is best-effort around it."""
    enable_retrieval(client)
    chat(client, session, FACT)
    bury(client, session)

    embedder.fail_with = RuntimeError("connection refused")
    events = chat(client, session, QUERY)

    assert [e for e in events if e["type"] == "token"], "no reply streamed"
    assert any(e["type"] == "done" for e in events)
    statuses = {e.get("status") for e in events if e["type"] == "memory"}
    assert "index_failed" in statuses or "retrieval_failed" in statuses

    # The turn was still persisted, so nothing was lost.
    msgs, _ = message_ids(session)
    assert any(content == QUERY for _, content in msgs)


def test_a_dead_embedder_surfaces_in_the_inspector(client, session, llm, embedder):
    enable_retrieval(client)
    chat(client, session, FACT)
    bury(client, session)

    embedder.fail_with = RuntimeError("connection refused")
    p = client.get(f"/api/sessions/{session}/prompt").json()
    assert p["retrieval_error"]
    assert "connection refused" in p["retrieval_error"]
    assert p["retrieved"] == []


def test_reindex_reports_a_dead_embedder(client, session, llm, embedder):
    """Explicit user action, so this one fails loudly rather than degrading."""
    enable_retrieval(client)
    embedder.fail_with = RuntimeError("connection refused")
    r = client.post(f"/api/sessions/{session}/reindex")
    assert r.status_code == 502
    assert "connection refused" in r.json()["detail"]


def test_retrieval_settings_survive_the_settings_round_trip(client):
    enable_retrieval(client, retrieval_top_k=7, retrieval_min_score=0.55)
    s = client.get("/api/settings").json()
    assert s["retrieval_enabled"] is True
    assert s["retrieval_top_k"] == 7
    assert s["retrieval_min_score"] == 0.55
