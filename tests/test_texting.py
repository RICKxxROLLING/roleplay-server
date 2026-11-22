"""Texting mode: the chat as a text thread, and switching naturally in and out."""
from __future__ import annotations

from conftest import sse_events

from app.chat.texting import TO_STORY, TO_TEXTING, clean_texts

SIDE = ("memory log", "fact sheet", "track the scene", "private journal", "you are scoring")


def chat_prompts(llm):
    return [p for p in llm.prompts if not any(t in p.lower() for t in SIDE)]


def say(client, sid, text):
    return sse_events(client.post(f"/api/sessions/{sid}/messages", json={"content": text}))


def texting(client, sid, on=True):
    r = client.patch(f"/api/sessions/{sid}", json={"mode": "texting" if on else "story"})
    assert r.status_code == 200, r.text
    return r.json()


# --- tidying what the model writes (cases from Stheno) ------------------------------

def test_clean_texts_keeps_plain_texts():
    t = "nah, probably half a day tops\nyou're a machine, riley"
    assert clean_texts(t, "Dina", "Riley") == t


def test_clean_texts_drops_its_own_label_on_later_lines():
    raw = "probably\nit's been acting up lately\n\nDina: hopefully not\nDina: you know me"
    assert clean_texts(raw, "Dina", "Riley") == "probably\nit's been acting up lately\nhopefully not\nyou know me"


def test_clean_texts_drops_meta_and_actions_and_quotes():
    raw = 'Yeah, just got home. What\'s on yr mind?\n\n(No further messages)'
    assert clean_texts(raw, "Garrick Flynn", "Riley") == "Yeah, just got home. What's on yr mind?"
    assert clean_texts('*grins* "be there in 5"', "Dina", "Riley") == "be there in 5"


def test_clean_texts_stops_where_the_user_would_answer():
    raw = "you up?\nRiley: yeah\nok good"
    assert clean_texts(raw, "Dina", "Riley") == "you up?"


def test_clean_texts_strips_a_full_name_label():
    assert clean_texts("Garrick Flynn: reckon so", "Garrick Flynn", "Riley") == "reckon so"


# --- the mode in a chat ---------------------------------------------------------------

def test_story_is_the_default(client, session):
    assert client.get(f"/api/sessions/{session}").json()["mode"] == "story"


def test_texting_prompt_replaces_the_prose_directives(client, session, llm):
    texting(client, session)
    say(client, session, "hey you up?")
    p = chat_prompts(llm)[-1]
    assert "are texting each other on their phones" in p
    assert "next text to You" in p
    assert "Write vivid, immersive prose" not in p
    assert "End on Seraphine's own words or actions" not in p


def test_texts_are_stored_tidy_and_marked(client, session, llm):
    texting(client, session)
    llm.replies = ['*smiles at her phone* "yeah, can\'t sleep"\nSeraphine: what\'s up?']
    say(client, session, "hey you up?")
    m = client.get(f"/api/sessions/{session}").json()["messages"][-1]
    assert m["texted"] is True
    # (MockLLM tags replies with " reply N", hence the prefix.)
    assert m["content"].startswith("yeah, can't sleep\nwhat's up")
    assert "*" not in m["content"]


def test_no_first_person_redo_while_texting(client, session, llm):
    texting(client, session)
    llm.replies = ["I'm on my way, I promise I won't be long, I just need my coat"]
    events = say(client, session, "where are you?")
    assert not any(e["type"] == "redo" for e in events)


def test_the_narration_guard_steps_aside(client, session, llm):
    texting(client, session)
    llm.replies = ["You there? You said you'd call"]
    say(client, session, "hey")
    assert client.get(f"/api/sessions/{session}").json()["messages"][-1]["content"].startswith("You there?")


def test_an_empty_text_is_redone(client, session, llm):
    texting(client, session)
    llm.replies = ["*sighs and puts the phone down*", "ok fine. 10 min"]
    events = say(client, session, "come over?")
    assert any(e["type"] == "redo" for e in events)
    assert client.get(f"/api/sessions/{session}").json()["messages"][-1]["content"].startswith("ok fine")


def test_switching_tells_the_model_once(client, session, llm):
    texting(client, session)
    # Real texts: the mock's default "*She nods once.*" is pure action, which
    # texting rightly strips to nothing -- and then nothing is saved.
    llm.replies = ["hey yourself", "maybe, why?", '*She opens the door.* "Took you long enough."']
    say(client, session, "hey")
    assert TO_TEXTING.replace("{{char}}", "Seraphine").replace("{{user}}", "You") in chat_prompts(llm)[-1]
    say(client, session, "you around later?")
    assert "gone their separate ways" not in chat_prompts(llm)[-1]  # only on the first
    texting(client, session, on=False)
    say(client, session, "*I knock on the archive door.*")
    p = chat_prompts(llm)[-1]
    assert "together in person again" in p and "Write vivid, immersive prose" in p
    m = client.get(f"/api/sessions/{session}").json()["messages"][-1]
    assert m["texted"] is False


def test_the_scene_pauses_while_texting(client, session, llm):
    texting(client, session)
    say(client, session, "hey")
    assert llm.scene_calls == []


def test_texts_are_short_and_story_is_not(client, session, llm, monkeypatch):
    from app.chat import texting as texting_mod

    seen = []
    original = llm.generate_stream

    def recording(prompt, params):
        seen.append(params.max_new_tokens)
        return original(prompt, params)

    monkeypatch.setattr(llm, "generate_stream", recording)
    llm.replies = ["hey", "sure", "Hello, archivist."]
    say(client, session, "hi")                      # story
    texting(client, session)
    say(client, session, "hey")                     # texting
    story_cap, text_cap = seen[0], [n for n in seen[1:] if n == texting_mod.MAX_NEW_TOKENS]
    assert story_cap == 220 and text_cap  # story: slow_pacing is on by default
    assert TO_STORY.startswith("{{char}} and {{user}} are together")


def test_the_users_own_texts_are_marked_too(client, session, llm):
    say(client, session, "*I wave.* Hi.")
    texting(client, session)
    llm.replies = ["hey"]
    say(client, session, "u up?")
    msgs = client.get(f"/api/sessions/{session}").json()["messages"]
    users = [m for m in msgs if m["role"] == "user"]
    assert [m["texted"] for m in users] == [False, True]


def test_a_described_photo_is_dropped_from_the_texts():
    from app.chat.texting import clean_texts

    got = clean_texts("[Photo of Sadie Wren, smiling, new haircut]\nHere's the new look! What do ya think?", "Sadie Wren", "Riley")
    assert got == "Here's the new look! What do ya think?"
    assert clean_texts("[laughs] ok fine", "Sadie Wren", "Riley") == "[laughs] ok fine"


# --- long distance: one long thread ---------------------------------------------------

def test_goodbyes_come_off_when_the_user_is_still_talking():
    from app.chat.texting import drop_partings

    assert drop_partings("just saw a squirrel\nc u soon!", "tell me something") == "just saw a squirrel"
    assert drop_partings("No worries, rest up. C u soon!\nNight, sweet dreams\nhaha anyway", "lol") == "haha anyway"
    # Things that only look like goodbyes stay.
    assert drop_partings("see you at 8\nnight shift again ugh", "hey") == "see you at 8\nnight shift again ugh"
    # The user is leaving: a goodbye back is only polite.
    assert drop_partings("ok night!\nsleep well", "im tired, going to bed") == "ok night!\nsleep well"
    # A reply that is only a goodbye is still the reply.
    assert drop_partings("ttyl!", "ok") == "ttyl!"


def test_a_goodbye_is_trimmed_in_the_chat(client, session, llm):
    texting(client, session)
    # The mock adds " reply N." to the last line, so the goodbye goes in the middle.
    llm.replies = ["haha same\nc u soon!\nwhat are you up to tonight?"]
    say(client, session, "lol i just had leftovers")
    got = client.get(f"/api/sessions/{session}").json()["messages"][-1]["content"]
    assert got.startswith("haha same\nwhat are you up to tonight") and "c u soon" not in got


def test_a_long_distance_thread_tells_the_model_they_live_far_apart(client, session, llm):
    texting(client, session)
    llm.replies = ["hey you"]
    say(client, session, "hey")
    assert "live far apart" not in chat_prompts(llm)[-1]
    r = client.patch(f"/api/sessions/{session}", json={"long_distance": True, "apart": "Seraphine is in Lisbon, You in Oslo."})
    assert r.json()["long_distance"] and r.json()["apart"] == "Seraphine is in Lisbon, You in Oslo."
    llm.replies = ["miss the sea"]
    say(client, session, "how's the weather there")
    p = chat_prompts(llm)[-1]
    assert "They live far apart -- Seraphine is in Lisbon, You in Oslo --" in p
    assert "never suggests meeting up" in p and "doesn't say goodbye or goodnight unless You does" in p
    assert "are apart; Seraphine is answering" not in p  # the long-distance tail replaces the plain one
    # Back in story mode the long-distance lines are gone (a visit, say).
    texting(client, session, on=False)
    llm.replies = ["*She waves.*"]
    say(client, session, "*I knock.*")
    assert "live far apart" not in chat_prompts(llm)[-1]


def test_a_chat_can_start_as_a_long_distance_thread(client, imported, llm):
    r = client.post("/api/sessions", json={"character_id": imported, "mode": "texting", "long_distance": True})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert r.json()["messages"] == []  # no greeting: a card's greeting is a scene in person
    s = client.get(f"/api/sessions/{sid}").json()
    assert s["mode"] == "texting" and s["long_distance"] and s["apart"] == ""
    llm.replies = ["omg hi"]
    say(client, sid, "hey stranger")
    p = chat_prompts(llm)[-1]
    assert "You lives hours away from Seraphine, too far to just drop by" in p
    assert "separate ways" not in p  # there was no scene to part from
    # Long distance only means something in a text thread.
    plain = client.post("/api/sessions", json={"character_id": imported, "long_distance": True}).json()["id"]
    assert not client.get(f"/api/sessions/{plain}").json()["long_distance"]


def test_writing_first_from_far_away_is_a_text(client, imported):
    import datetime as dt

    from app.chat import autonomy
    from app.db.database import SessionLocal
    from app.db.models import ChatSession

    sid = client.post("/api/sessions", json={"character_id": imported, "mode": "texting", "long_distance": True}).json()["id"]
    client.post(f"/api/sessions/{sid}/messages", json={"content": "hey"})
    with SessionLocal() as db:
        s = db.get(ChatSession, sid)
        p = autonomy.build_decide_prompt(s, s.character, "Riley", dt.datetime.now(dt.timezone.utc))
        assert "send them a text?" in p and "come find them" not in p


def test_long_distance_survives_export_and_import(client, imported, llm):
    sid = client.post("/api/sessions", json={"character_id": imported, "mode": "texting", "long_distance": True,
                                              "apart": "an ocean apart"}).json()["id"]
    llm.replies = ["hi!"]
    say(client, sid, "hey")
    exported = client.get(f"/api/sessions/{sid}/export").json()
    assert exported["long_distance"] is True and exported["apart"] == "an ocean apart"
