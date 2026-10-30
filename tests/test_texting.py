"""Texting mode: the chat as a text thread, and switching naturally in and out."""
from __future__ import annotations

from conftest import sse_events

from app.chat.texting import TO_STORY, TO_TEXTING, clean_texts

SIDE = ("memory log", "fact sheet", "track the scene")


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
    assert story_cap == 400 and text_cap
    assert TO_STORY.startswith("{{char}} and {{user}} are together")


def test_the_users_own_texts_are_marked_too(client, session, llm):
    say(client, session, "*I wave.* Hi.")
    texting(client, session)
    llm.replies = ["hey"]
    say(client, session, "u up?")
    msgs = client.get(f"/api/sessions/{session}").json()["messages"]
    users = [m for m in msgs if m["role"] == "user"]
    assert [m["texted"] for m in users] == [False, True]
