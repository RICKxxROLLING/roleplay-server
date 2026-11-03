"""Earned trust: the model proposes, the server caps, and closeness has to be earned."""
from __future__ import annotations

import datetime as dt

from conftest import sse_events

from app.db import SessionLocal
from app.db.models import ChatSession, Message
from app.memory import trust

SIDE = ("memory log", "fact sheet", "track the scene", "private journal", "you are scoring")


def chat_prompts(llm):
    return [p for p in llm.prompts if not any(t in p.lower() for t in SIDE)]


def say(client, sid, text):
    return sse_events(client.post(f"/api/sessions/{sid}/messages", json={"content": text}))


def standing(client, sid):
    return client.get(f"/api/sessions/{sid}/journal").json()[0]["standing"]


# --- the server's half -------------------------------------------------------------

def test_gains_are_capped_and_shrink_as_a_score_rises():
    low = trust.apply_change({"trust": 30, "affection": 30, "respect": 30}, {"trust": 50, "affection": 0, "respect": 0})
    assert low["trust"] == 31.4  # capped to +2, worth 1.4 at 30
    high = trust.apply_change({"trust": 80, "affection": 30, "respect": 30}, {"trust": 2, "affection": 0, "respect": 0})
    assert high["trust"] == 80.4


def test_a_small_gain_counts_half():
    out = trust.apply_change({"trust": 50, "affection": 50, "respect": 50}, {"trust": 1, "affection": 2, "respect": 0})
    assert out["trust"] == 50.25 and out["affection"] == 51.0


def test_losses_land_in_full_up_to_their_cap():
    out = trust.apply_change({"trust": 80, "affection": 80, "respect": 2}, {"trust": -3, "affection": -50, "respect": -5})
    assert out == {"trust": 77.0, "affection": 75.0, "respect": 0.0}


def test_trust_is_slow_to_earn_and_quick_to_lose():
    s = {"trust": 30.0, "affection": 30.0, "respect": 30.0}
    for _ in range(10):
        s = trust.apply_change(s, {"trust": 2, "affection": 0, "respect": 0})
    earned = s["trust"] - 30
    for _ in range(3):
        s = trust.apply_change(s, {"trust": -3, "affection": 0, "respect": 0})
    assert earned < 13 and s["trust"] < 30 + earned - 8


def test_parse_needs_all_three_numbers():
    ok = trust.parse_change(" helped out\nTrust: +2\nAffection: 1\nRespect: -1")
    assert ok == {"reason": "helped out", "trust": 2, "affection": 1, "respect": -1}
    assert trust.parse_change(" helped\nTrust: 2\nAffection: 1") is None
    assert trust.parse_change(" helped\nTrust: a lot\nAffection: 1\nRespect: 1") is None


# --- thresholds ----------------------------------------------------------------------

def line(t, a, r):
    return trust.standing_line({"trust": t, "affection": a, "respect": r}, "Dina", "Riley")


def test_secrets_have_to_be_earned():
    assert "does not trust Riley" in line(10, 30, 30)
    assert "keeps secrets and old wounds private" in line(40, 30, 30)
    assert "may share secrets" in line(60, 30, 30)


def romance_line(t, a, r):
    return trust.standing_line({"trust": t, "affection": a, "respect": r}, "Dina", "Riley", romance=True)


def test_romance_needs_the_card_to_opt_in_and_both_scores():
    # No opt-in: says no at every level of affection, however high.
    for t, a in ((90, 90), (40, 40), (10, 10)):
        assert "kindly says no" in line(t, a, 30)
    assert "may let romantic" not in line(90, 90, 90)
    # Opted in: nothing tells the character to refuse, and high scores open the door.
    for t, a in ((90, 90), (40, 40), (10, 10)):
        assert "kindly says no" not in romance_line(t, a, 30)
    assert "may let romantic" not in romance_line(40, 80, 30)
    assert "may let romantic" not in romance_line(60, 60, 30)
    assert "may let romantic" in romance_line(60, 75, 30)


MINORS = [
    "{{char}} is a Seraphite boy of about thirteen, slight and sharp-eyed.",
    "{{char}} is in her late teens, a young fighter.",
    "{{char}} is 16 years old and new in town.",
    "{{char}} is a sixteen-year-old apprentice.",
    "{{char}} is a teenager who works the docks.",
    "{{char}} is a young girl who lives by the harbor.",
    "A shy kid, {{char}} is about fifteen.",
]
ADULTS = [
    "{{char}} is in his fifties, a broad Texan who lost his son.",
    "{{char}} is in her early twenties. She grew up in Jackson after arriving there as a kid.",
    "{{char}} has two children and worked here for seventeen years.",
    "{{char}} is about twenty, born after the outbreak.",
]


def test_cards_written_as_minors_are_recognised():
    from conftest import make_card

    for text in MINORS:
        assert trust.is_minor(make_card(description=text)), text
    for text in ADULTS:
        assert not trust.is_minor(make_card(description=text)), text


def test_a_minor_cannot_unlock_romance_even_with_the_tag():
    from conftest import make_card

    assert trust.romance_allowed(make_card(description=ADULTS[1], tags=["Romance "]))
    assert not trust.romance_allowed(make_card(description=ADULTS[1], tags=["jackson"]))
    assert not trust.romance_allowed(make_card(description=MINORS[0], tags=["romance"]))


def test_a_minor_stays_platonic_at_any_score_with_trust_on_or_off(client, llm):
    kid = client.post("/api/characters", json={
        "name": "Lev", "first_mes": "Hi.", "tags": ["romance"],
        "description": "{{char}} is a boy of about thirteen who climbs everything."}).json()["id"]
    sid = client.post("/api/sessions", json={"character_id": kid}).json()["id"]
    client.put(f"/api/sessions/{sid}/standing/{kid}", json={"trust": 100, "affection": 100, "respect": 100})
    say(client, sid, "Hi.")
    p = chat_prompts(llm)[-1]
    assert "stays platonic" in p and "romantic" not in p
    client.patch("/api/settings", json={"trust_enabled": False})
    say(client, sid, "Hi again.")
    p = chat_prompts(llm)[-1]
    assert "stays platonic" in p and "romantic" not in p


def test_the_standing_sits_in_the_tail(client, session, llm):
    say(client, session, "Hi.")
    say(client, session, "Hi again.")
    p = chat_prompts(llm)[-1]
    assert p.index("Hi again.") < p.index("keeps secrets and old wounds private")


def test_cold_characters_write_less_often():
    assert trust.gap_factor({"affection": 80}) == 0.75
    assert trust.gap_factor({"affection": 40}) == 1.0
    assert trust.gap_factor({"affection": 10}) == 2.0
    assert trust.gap_factor(None) == 1.0


# --- in a chat -------------------------------------------------------------------------

def test_first_exchange_seeds_then_scores(client, session, llm):
    llm.seed_reply = " childhood friends\nTrust: 60\nAffection: 65\nRespect: 55"
    llm.trust_reply = " fixed the latch\nTrust: 2\nAffection: 0\nRespect: 0"
    say(client, session, "I fixed your latch.")
    s = standing(client, session)
    assert s["trust"] == 60.8 and s["affection"] == 65 and s["last_reason"] == "fixed the latch"


def test_a_seed_cannot_start_at_the_top(client, session, llm):
    llm.seed_reply = " soulmates\nTrust: 100\nAffection: 100\nRespect: 100"
    say(client, session, "Hi.")
    assert standing(client, session)["trust"] == 70


def test_scoring_sees_the_users_line_and_what_it_answered_not_the_reply(client, session, llm):
    llm.replies = ["*She kept her promise and fixed the roof herself.*"]
    say(client, session, "Hello there.")
    p = llm.trust_calls[-1]
    assert "Hello there." in p and "You are late" in p and "fixed the roof" not in p


def test_the_standing_reaches_the_next_prompt(client, session, llm):
    say(client, session, "Hi.")
    say(client, session, "Hi again.")
    assert "keeps secrets and old wounds private" in chat_prompts(llm)[-1]


def test_a_swipe_does_not_score_again(client, session, llm):
    say(client, session, "Hi.")
    n = len(llm.trust_calls)
    sse_events(client.post(f"/api/sessions/{session}/regenerate"))
    assert len(llm.trust_calls) == n


def test_off_means_no_pass_and_no_line(client, session, llm):
    client.patch("/api/settings", json={"trust_enabled": False})
    say(client, session, "Hi.")
    say(client, session, "Hi again.")
    assert llm.trust_calls == [] and standing(client, session) is None
    assert "keeps secrets" not in chat_prompts(llm)[-1]


def test_a_misfire_changes_nothing(client, session, llm):
    say(client, session, "Hi.")
    before = standing(client, session)
    llm.trust_reply = " I cannot score this"
    say(client, session, "Hi again.")
    assert standing(client, session)["trust"] == before["trust"]


def test_set_by_hand(client, session, llm, imported):
    r = client.put(f"/api/sessions/{session}/standing/{imported}", json={"trust": 80, "affection": 75, "respect": 50})
    assert r.status_code == 200 and standing(client, session)["trust"] == 80
    assert client.put(f"/api/sessions/{session}/standing/{imported}", json={"trust": 180, "affection": 1, "respect": 1}).status_code == 422
    say(client, session, "Hi.")
    assert "the way family does" in chat_prompts(llm)[-1]


def test_group_members_stand_separately(client, imported, llm):
    tommy = client.post("/api/characters", json={"name": "Tommy", "first_mes": "Hey."}).json()["id"]
    sid = client.post("/api/sessions", json={"character_id": imported, "member_ids": [tommy]}).json()["id"]
    llm.trust_reply = " helped\nTrust: 2\nAffection: 0\nRespect: 0"
    say(client, sid, "Tommy, I fixed your rifle.")
    by = {j["name"]: j["standing"] for j in client.get(f"/api/sessions/{sid}/journal").json()}
    assert by["Tommy"]["trust"] == 31.4 and by["Seraphine"] is None


def test_being_left_on_read_costs_affection_once(client, session, llm, monkeypatch):
    import asyncio

    from app import push
    from app.chat import autonomy

    async def no_ping(db):
        return 0

    monkeypatch.setattr(push, "ping_all", no_ping)
    say(client, session, "Hi.")
    start = standing(client, session)["affection"]
    with SessionLocal() as db:
        s = db.get(ChatSession, session)
        for _ in range(2):
            db.add(Message(session_id=session, role="assistant", content="Hey?", initiated=True))
        db.commit()
        llm.replies = ["You there?"]
        asyncio.run(autonomy.write_first(db, s, "", dt.datetime.now(dt.timezone.utc)))
    assert standing(client, session)["affection"] == start - 1


def test_deleting_the_chat_deletes_its_standing(client, session, llm):
    from app.db.models import Standing

    say(client, session, "Hi.")
    client.delete(f"/api/sessions/{session}")
    with SessionLocal() as db:
        assert db.query(Standing).count() == 0


def test_a_minors_line_has_no_romantic_words_at_any_score():
    for t, a in ((100, 100), (40, 40), (10, 10)):
        got = trust.standing_line({"trust": t, "affection": a, "respect": 30}, "Lev", "Riley", minor=True)
        assert "romantic" not in got and "kiss" not in got and "family" not in got
