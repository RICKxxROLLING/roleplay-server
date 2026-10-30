"""A character's own thoughts: the private journal and the view fed back in."""
from __future__ import annotations

from conftest import sse_events

from app.memory.thoughts import parse_thought

SIDE = ("memory log", "fact sheet", "track the scene", "private journal")


def chat_prompts(llm):
    return [p for p in llm.prompts if not any(t in p.lower() for t in SIDE)]


def say(client, sid, text):
    return sse_events(client.post(f"/api/sessions/{sid}/messages", json={"content": text}))


def journal(client, sid):
    return client.get(f"/api/sessions/{sid}/journal").json()


# --- parsing (shapes seen on Stheno) ------------------------------------------------

def test_parses_journal_and_view():
    t = parse_thought("Journal: He fixed the stove without being asked. I didn't know what to say.\n"
                      "View: Privately, Dina thinks Riley is kinder than he lets on.", "Dina")
    assert t.journal.startswith("He fixed the stove") and t.view.startswith("Privately, Dina")


def test_a_view_in_first_person_is_refused():
    """The view goes into every prompt; first person there pulls the narrator into it."""
    assert parse_thought("Journal: A long enough journal entry about the day.\n"
                         "View: Privately, I think Riley is kind.", "Dina") is None


def test_a_view_about_someone_else_or_missing_is_refused():
    j = "Journal: A long enough journal entry about the day and the cocoa.\n"
    assert parse_thought(j + "View: Riley seems kind.", "Dina") is None
    assert parse_thought(j, "Dina") is None
    assert parse_thought("Journal: short\nView: Privately, Dina likes him.", "Dina") is None


def test_full_name_cards_match_on_first_name():
    t = parse_thought("Journal: The boy asked about my hand with more tact than most grown men.\n"
                      "View: Privately, Garrick thinks Riley has quiet strength.", "Garrick Flynn")
    assert t is not None


# --- in a chat ------------------------------------------------------------------------

def test_each_reply_writes_an_entry_and_the_view_feeds_the_next_prompt(client, session, llm):
    say(client, session, "Hello.")
    j = journal(client, session)[0]
    assert j["name"] == "Seraphine" and len(j["entries"]) == 1
    assert j["view"] == "Privately, Seraphine is starting to trust them, a little (1)."
    say(client, session, "Still there?")
    p = chat_prompts(llm)[-1]
    assert "What Seraphine privately thinks of You, unknown to You: Privately, Seraphine is starting to trust them, a little (1)." in p
    # The journal itself, first person, never goes back in.
    assert "conversation stayed with me" not in p


def test_each_pass_sees_the_previous_view(client, session, llm):
    say(client, session, "Hello.")
    say(client, session, "Again.")
    assert "(1)" in llm.thought_calls[1] and "nothing much yet" in llm.thought_calls[0]


def test_a_swipe_replaces_its_entry(client, session, llm):
    say(client, session, "Hello.")
    sse_events(client.post(f"/api/sessions/{session}/regenerate"))
    entries = journal(client, session)[0]["entries"]
    assert len(entries) == 1 and entries[0]["view"].endswith("(2).")
    # The swipe's pass was written from the view *before* the replaced reply.
    assert "nothing much yet" in llm.thought_calls[1]


def test_off_means_no_pass_and_no_view(client, session, llm):
    client.patch("/api/settings", json={"thoughts_enabled": False})
    say(client, session, "Hello.")
    assert llm.thought_calls == [] and journal(client, session)[0]["entries"] == []


def test_forget_starts_afresh(client, session, llm, imported):
    say(client, session, "Hello.")
    assert client.delete(f"/api/sessions/{session}/journal/{imported}").json() == {"forgotten": 1}
    say(client, session, "Hi again.")
    assert "nothing much yet" in llm.thought_calls[-1]


def test_group_members_keep_separate_journals(client, imported, llm):
    tommy = client.post("/api/characters", json={"name": "Tommy", "first_mes": "Hey."}).json()["id"]
    sid = client.post("/api/sessions", json={"character_id": imported, "member_ids": [tommy]}).json()["id"]
    say(client, sid, "Tommy, hi.")
    say(client, sid, "Seraphine?")
    by = {j["name"]: j for j in journal(client, sid)}
    assert by["Tommy"]["view"].startswith("Privately, Tommy")
    assert by["Seraphine"]["view"].startswith("Privately, Seraphine")


def test_deleting_the_chat_deletes_its_journal(client, session, llm):
    from app.db import SessionLocal
    from app.db.models import CharacterThought

    say(client, session, "Hello.")
    client.delete(f"/api/sessions/{session}")
    with SessionLocal() as db:
        assert db.query(CharacterThought).count() == 0
