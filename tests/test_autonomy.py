"""Characters writing first: the rules, the decision, the message, suspension."""
from __future__ import annotations

import datetime as dt

import pytest

from app import push
from app.chat import autonomy
from app.config import settings
from app.db import SessionLocal
from app.db.models import ChatSession, Message

UTC = dt.timezone.utc
# A Tuesday, 15:00 UTC -- 09:00 in Denver, outside the default quiet hours.
NOON = dt.datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def zone(monkeypatch):
    monkeypatch.setattr(settings, "timezone", "America/Denver")


@pytest.fixture(autouse=True)
def no_relay(monkeypatch):
    pings = []

    async def fake_ping_all(db):
        pings.append(1)
        return 1

    monkeypatch.setattr(push, "ping_all", fake_ping_all)
    return pings


def make_chat(client, session, *, on=True, last_at=NOON - dt.timedelta(hours=3), user_last=True):
    """The fixture chat, with times set so the rules can be tested exactly."""
    client.post(f"/api/sessions/{session}/messages", json={"content": "Hello again."})
    with SessionLocal() as db:
        s = db.get(ChatSession, session)
        s.autonomous = on
        n = len(s.messages)
        for i, m in enumerate(s.messages):
            m.created_at = (last_at - dt.timedelta(minutes=n - 1 - i)).replace(tzinfo=None)
        if not user_last:
            pass
        db.commit()


def status(client, session):
    return client.get(f"/api/sessions/{session}").json()


def chat(db, session):
    return db.get(ChatSession, session)


# --- the rules --------------------------------------------------------------------

def test_off_by_default(client, session, llm):
    assert status(client, session)["autonomous"] is False
    with SessionLocal() as db:
        assert autonomy.eligible(chat(db, session), NOON) == "off for this chat"


def test_eligible_after_the_gap(client, session, llm):
    make_chat(client, session)
    with SessionLocal() as db:
        assert autonomy.eligible(chat(db, session), NOON) is None


def test_too_soon(client, session, llm):
    make_chat(client, session, last_at=NOON - dt.timedelta(minutes=30))
    with SessionLocal() as db:
        assert autonomy.eligible(chat(db, session), NOON) == "too soon since the last message"


def test_quiet_hours_in_the_owners_zone(client, session, llm):
    make_chat(client, session)
    late = dt.datetime(2026, 10, 28, 5, 0, tzinfo=UTC)  # 23:00 in Denver
    with SessionLocal() as db:
        assert autonomy.eligible(chat(db, session), late) == "quiet hours"


def test_quiet_hours_that_cross_midnight():
    def at(h, m=0):
        return dt.datetime(2026, 10, 27, h, m)
    assert autonomy.in_quiet_hours(at(23)) and autonomy.in_quiet_hours(at(3))
    assert not autonomy.in_quiet_hours(at(8, 0)) and not autonomy.in_quiet_hours(at(22, 29))


def test_the_gap_doubles_with_each_unanswered_message(client, session, llm):
    make_chat(client, session)
    with SessionLocal() as db:
        s = chat(db, session)
        assert autonomy.gap_needed(s) == dt.timedelta(hours=2)
        for _ in range(2):
            db.add(Message(session_id=s.id, role="assistant", content="Hey?", initiated=True))
        db.commit()
        db.refresh(s)
        assert autonomy.unanswered(s) == 2 and autonomy.gap_needed(s) == dt.timedelta(hours=8)


def test_three_unanswered_suspend_until_the_user_replies(client, session, llm):
    make_chat(client, session)
    with SessionLocal() as db:
        for _ in range(3):
            db.add(Message(session_id=session, role="assistant", content="Hey?", initiated=True))
        db.commit()
        assert autonomy.eligible(chat(db, session), NOON + dt.timedelta(days=3)).startswith("waiting for a reply")
    st = status(client, session)
    assert st["suspended"] is True and st["unanswered"] == 3
    client.post(f"/api/sessions/{session}/messages", json={"content": "Sorry, I'm here."})
    assert status(client, session)["suspended"] is False


def test_daily_limit(client, session, llm, monkeypatch):
    monkeypatch.setattr(settings, "autonomy_max_per_day", 1)
    make_chat(client, session)
    with SessionLocal() as db:
        db.add(Message(session_id=session, role="assistant", content="Morning!", initiated=True,
                       created_at=(NOON - dt.timedelta(hours=5)).replace(tzinfo=None)))
        db.add(Message(session_id=session, role="user", content="Hi!",
                       created_at=(NOON - dt.timedelta(hours=4)).replace(tzinfo=None)))
        db.commit()
        assert autonomy.eligible(chat(db, session), NOON) == "daily limit reached"


def test_never_while_a_reply_generates(client, session, llm, monkeypatch):
    make_chat(client, session)
    monkeypatch.setattr(autonomy, "ACTIVE", 1)
    with SessionLocal() as db:
        assert autonomy.eligible(chat(db, session), NOON) == "a reply is generating"


# --- the decision and the message -------------------------------------------------

def test_decision_parsing():
    assert autonomy.parse_decision(" YES - she wants to ask about the job.") == (True, "she wants to ask about the job.")
    assert autonomy.parse_decision(" no. He is busy at the forge.") == (False, "He is busy at the forge.")
    assert autonomy.parse_decision(" Maybe later") == (False, "")


def test_a_no_writes_nothing_and_waits_irregularly(client, session, llm):
    make_chat(client, session)
    llm.replies = [" NO -- she is busy with JJ."]
    with SessionLocal() as db:
        before = len(chat(db, session).messages)
        import asyncio
        r = asyncio.run(autonomy.check(db, chat(db, session), NOON))
        s = chat(db, session)
        assert r["sent"] is False and "busy with JJ" in r["why"]
        assert len(s.messages) == before
        wait = autonomy._utc(s.next_check_at) - NOON
        assert dt.timedelta(minutes=30) <= wait <= dt.timedelta(minutes=100)


def test_a_yes_writes_marks_and_notifies(client, session, llm, no_relay):
    make_chat(client, session)
    llm.replies = [" YES - to ask about the ledger.", '"Did you find the ledger?" *She waits by the window.*']
    import asyncio
    with SessionLocal() as db:
        r = asyncio.run(autonomy.check(db, chat(db, session), NOON))
    # (MockLLM tags every reply with " reply N.*", hence startswith.)
    assert r["sent"] is True and r["why"].startswith("to ask about the ledger")
    last = status(client, session)["messages"][-1]
    assert last["role"] == "assistant" and last["initiated"] is True
    # The reason and the time reached the prompt...
    p = [x for x in llm.prompts if "reaches out to" in x][-1]
    assert "to ask about the ledger" in p and "Tuesday, 9:00 AM" in p
    # ...and the notification carries the words, not the stage directions.
    got = client.post("/api/push/pending").json()["notifications"]
    assert got[0]["title"] == "Seraphine" and got[0]["body"].startswith("Did you find the ledger?")
    assert got[0]["session_id"] == session and no_relay == [1]


def test_message_me_now_skips_rules_and_decision(client, session, llm):
    client.post(f"/api/sessions/{session}/messages", json={"content": "Hello."})  # just now
    r = client.post(f"/api/sessions/{session}/write-first", json={"force": True}).json()
    assert r["sent"] is True
    assert not any("Answer YES or NO" in p for p in llm.prompts)
    assert status(client, session)["unanswered"] == 1


def test_the_toggle(client, session, llm):
    assert client.patch(f"/api/sessions/{session}", json={"autonomous": True}).json()["autonomous"] is True
    assert status(client, session)["autonomous"] is True


def test_tick_checks_only_chats_that_opted_in(client, session, llm, imported):
    other = client.post("/api/sessions", json={"character_id": imported}).json()["id"]
    make_chat(client, session)
    llm.replies = [" NO - not now."]
    import asyncio
    with SessionLocal() as db:
        results = asyncio.run(autonomy.tick(db))
    assert [r["session_id"] for r in results] == [session] and other != session


# --- time in ordinary replies -------------------------------------------------------

def test_a_long_silence_tells_the_model_the_time(client, session, llm):
    client.post(f"/api/sessions/{session}/messages", json={"content": "Night."})
    with SessionLocal() as db:
        s = chat(db, session)
        for m in s.messages:
            m.created_at = (dt.datetime.now(UTC) - dt.timedelta(hours=20)).replace(tzinfo=None)
        db.commit()
    client.post(f"/api/sessions/{session}/messages", json={"content": "Morning!"})
    p = [x for x in llm.prompts if "Morning!" in x and "track the scene" not in x.lower() and "private journal" not in x.lower()][0]
    assert "have passed since the previous message" in p and "about 20 hours" in p


def test_mid_conversation_says_nothing_about_time(client, session, llm):
    client.post(f"/api/sessions/{session}/messages", json={"content": "Hi."})
    client.post(f"/api/sessions/{session}/messages", json={"content": "And another."})
    p = [x for x in llm.prompts if "And another." in x and "track the scene" not in x.lower() and "private journal" not in x.lower()][0]
    assert "have passed since" not in p
