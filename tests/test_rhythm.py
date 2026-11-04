"""A character's own day: reading it from the model, and what it does to writing first."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace as NS

import pytest

from app import push
from app.chat import autonomy, rhythm
from app.config import settings
from app.db import SessionLocal
from app.db.models import Character, ChatSession

UTC = dt.timezone.utc
# A Tuesday, 15:00 UTC: 09:00 in Denver, outside the default quiet hours.
NINE = dt.datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def at(h, m=0):
    return dt.datetime(2026, 10, 27, h, m)


@pytest.fixture(autouse=True)
def zone(monkeypatch):
    monkeypatch.setattr(settings, "timezone", "America/Denver")


@pytest.fixture(autouse=True)
def no_relay(monkeypatch):
    async def fake_ping_all(db):
        return 1

    monkeypatch.setattr(push, "ping_all", fake_ping_all)


def make_chat(client, session, last_at=NINE - dt.timedelta(hours=5)):
    client.post(f"/api/sessions/{session}/messages", json={"content": "Hello again."})
    with SessionLocal() as db:
        s = db.get(ChatSession, session)
        s.autonomous = True
        for i, m in enumerate(s.messages):
            m.created_at = (last_at - dt.timedelta(minutes=len(s.messages) - 1 - i)).replace(tzinfo=None)
        db.commit()


def set_day(session, **day):
    with SessionLocal() as db:
        s = db.get(ChatSession, session)
        s.character.rhythm = day
        db.commit()


def why(session, now=NINE):
    with SessionLocal() as db:
        return autonomy.eligible(db.get(ChatSession, session), now)


# --- reading an answer --------------------------------------------------------------


@pytest.mark.parametrize("text,want", [
    ("6:30 AM", "06:30"),
    ("around 7 pm", "19:00"),
    ("12:00 AM (midnight)", "00:00"),
    ("12:15 PM", "12:15"),
    ("Dawn, around 6:00 am", "06:00"),
    ("10:00 AM to 6:00 PM (sessions)", "10:00"),
    ("18:30", "18:30"),
    ("by dusk", "19:00"),
    ("late, sometimes past midnight", "00:00"),
    ("mid-morning, often after sleeping in", "09:30"),
    ("whenever", None),
    ("13:00 pm", None),
])
def test_reading_a_time(text, want):
    assert rhythm.read_time(text) == want


def test_a_full_answer():
    got = rhythm.parse(" Patrols by night.\nWakes: 10:00 AM\nSleeps: 3:00 AM\n"
                       "Busy from: 6:00 PM (patrol)\nBusy until: 2:00 AM\nReaches out: Rarely, keeps to herself")
    assert got == {"wake": "10:00", "sleep": "03:00", "busy_from": "18:00", "busy_until": "02:00",
                   "reach": "rarely", "reason": "Patrols by night."}


@pytest.mark.parametrize("raw,gone", [
    ("x\nWakes: 6:00 AM\nSleeps: whenever\nBusy from: 8:00 AM\nBusy until: 5:00 PM", "wake"),  # half a pair
    ("x\nWakes: 6:00 AM\nSleeps: 9:00 AM\nBusy from: 8:00 AM\nBusy until: 5:00 PM", "wake"),  # up three hours
    ("x\nWakes: 6:00 AM\nSleeps: 10:00 PM\nBusy from: 7:00 AM\nBusy until: 6:00 AM", "busy_from"),  # busy all day
])
def test_what_makes_no_sense_is_dropped(raw, gone):
    got = rhythm.parse(raw)
    assert gone not in got and got


def test_occasionally_means_sometimes_and_nonsense_means_nothing():
    assert rhythm.parse("x\nWakes: 6 AM\nSleeps: 10 PM\nReaches out: occasionally")["reach"] == "sometimes"
    assert "reach" not in rhythm.parse("x\nWakes: 6 AM\nSleeps: 10 PM\nReaches out: Garrick does")
    assert rhythm.parse("I can't say.") == {}


# --- windows ------------------------------------------------------------------------


def test_asleep_across_midnight():
    owl = NS(rhythm={"wake": "10:00", "sleep": "03:00"})
    assert rhythm.asleep(owl, at(5)) and rhythm.asleep(owl, at(9, 59))
    assert not rhythm.asleep(owl, at(10)) and not rhythm.asleep(owl, at(23)) and not rhythm.asleep(owl, at(2))


def test_no_rhythm_is_never_asleep_or_busy():
    plain = NS(id=1, rhythm=None)
    assert not rhythm.asleep(plain, at(3)) and not rhythm.busy(plain, at(3), 1)


def test_busy_lets_them_slip_away_about_one_hour_in_five():
    worker = NS(id=1, rhythm={"busy_from": "00:00", "busy_until": "23:59"})
    hours = [dt.datetime(2026, 10, d, h) for d in range(1, 29) for h in range(24)]
    free = sum(not rhythm.busy(worker, t, 7) for t in hours)
    assert 0.12 < free / len(hours) < 0.28
    # Steady within the hour, so a check doesn't reroll its way through.
    assert rhythm.busy(worker, at(9, 0), 7) == rhythm.busy(worker, at(9, 45), 7)


def test_jitter_is_steady_between_checks_and_changes_after_a_message():
    def chat(last):
        return NS(id=3, messages=[NS(id=last)])

    assert rhythm.jitter(chat(10)) == rhythm.jitter(chat(10))
    many = {rhythm.jitter(chat(i)) for i in range(50)}
    assert len(many) > 40 and all(0.8 <= j <= 1.4 for j in many)


# --- writing first ------------------------------------------------------------------


def test_a_chat_with_no_rhythm_is_unchanged(client, session, llm):
    make_chat(client, session)
    assert why(session) is None


def test_asleep_says_so(client, session, llm):
    make_chat(client, session)
    set_day(session, wake="10:00", sleep="03:00")
    assert why(session) == "Seraphine is asleep until 10:00 AM"


def test_busy_says_so(client, session, llm, monkeypatch):
    make_chat(client, session)
    monkeypatch.setattr(rhythm, "SLIP_CHANCE", 0)
    set_day(session, busy_from="08:00", busy_until="17:00")
    assert why(session) == "Seraphine is busy until 5:00 PM"


def test_quiet_hours_still_win_over_a_night_owl(client, session, llm):
    make_chat(client, session)
    set_day(session, wake="10:00", sleep="03:00")
    late = dt.datetime(2026, 10, 28, 5, 0, tzinfo=UTC)  # 23:00 in Denver
    assert why(session, late) == "quiet hours"


def test_reaching_out_rarely_waits_longer(client, session, llm):
    make_chat(client, session)
    with SessionLocal() as db:
        s = db.get(ChatSession, session)
        base = autonomy.gap_needed(s)
        s.character.rhythm = {"reach": "rarely"}
        assert autonomy.gap_needed(s) == base * 1.75
        s.character.rhythm = {"reach": "often"}
        assert autonomy.gap_needed(s) == base * 0.75


def test_message_me_now_wakes_them(client, session, llm):
    make_chat(client, session)
    set_day(session, wake="10:00", sleep="03:00")
    with SessionLocal() as db:
        import asyncio

        got = asyncio.run(autonomy.check(db, db.get(ChatSession, session), NINE, force=True))
    assert got["sent"] is True


def test_the_question_mentions_their_day(client, session, llm):
    make_chat(client, session)
    set_day(session, wake="07:00", sleep="23:00", busy_from="20:00", busy_until="22:00")
    with SessionLocal() as db:
        s = db.get(ChatSession, session)
        prompt = autonomy.build_decide_prompt(s, s.character, "Riley", NINE)
    assert "usually gets up at 7:00 AM and goes to bed at 11:00 PM" in prompt
    assert "tied up from 8:00 PM to 10:00 PM" in prompt


def test_in_a_group_only_someone_awake_is_chosen(client, session, llm):
    make_chat(client, session)
    other = client.post("/api/characters", json={"name": "Tomas", "first_mes": "Hi."}).json()["id"]
    client.put(f"/api/sessions/{session}/members", json={"member_ids": [other]})
    set_day(session, wake="10:00", sleep="03:00")
    with SessionLocal() as db:
        s = db.get(ChatSession, session)
        local = autonomy.local_now(NINE)
        assert [c.name for c in rhythm.free_cast(s, local)] == ["Tomas"]
        assert autonomy.eligible(s, NINE) is None
        db.get(Character, other).rhythm = {"wake": "10:00", "sleep": "03:00"}
        db.commit()
        db.refresh(s)
        assert autonomy.eligible(s, NINE) == "everyone here is asleep or busy"


# --- the API ------------------------------------------------------------------------


def char_id(client, session):
    return client.get(f"/api/sessions/{session}").json()["character_id"]


def test_suggesting_saves_nothing(client, session, llm):
    cid = char_id(client, session)
    got = client.post(f"/api/characters/{cid}/rhythm/suggest").json()
    assert got["wake"] == "10:00" and got["reach"] == "rarely" and got["reason"]
    assert client.get(f"/api/characters/{cid}").json()["rhythm"] == {}


def test_a_useless_suggestion_says_so(client, session, llm):
    llm.rhythm_reply = "I can't say."
    cid = char_id(client, session)
    assert client.post(f"/api/characters/{cid}/rhythm/suggest").status_code == 502


def test_a_partial_answer_gets_a_second_try(client, session, llm):
    llm.rhythm_replies = ["x\nWakes: 7 AM\nSleeps: 11 PM", "x\nWakes: 8 AM\nSleeps: 11 PM\nBusy from: 9 AM\nBusy until: 5 PM"]
    cid = char_id(client, session)
    assert client.post(f"/api/characters/{cid}/rhythm/suggest").json()["wake"] == "08:00"
    assert len(llm.rhythm_calls) == 2


def test_saving_and_clearing(client, session, llm):
    cid = char_id(client, session)
    day = {"wake": "07:00", "sleep": "23:00", "reach": "often"}
    assert client.patch(f"/api/characters/{cid}", json={"rhythm": day}).json()["rhythm"] == day
    assert client.get(f"/api/characters/{cid}").json()["rhythm"] == day
    assert "rhythm" not in client.get(f"/api/characters/{cid}").json()["card"]
    client.patch(f"/api/characters/{cid}", json={"rhythm": {}})
    assert client.get(f"/api/characters/{cid}").json()["rhythm"] == {}


@pytest.mark.parametrize("bad,code", [
    ({"wake": "07:00"}, 400),
    ({"busy_until": "17:00"}, 400),
    ({"wake": "25:00", "sleep": "23:00"}, 422),
    ({"reach": "constantly"}, 422),
])
def test_bad_rhythms_are_refused(client, session, llm, bad, code):
    cid = char_id(client, session)
    assert client.patch(f"/api/characters/{cid}", json={"rhythm": bad}).status_code == code
