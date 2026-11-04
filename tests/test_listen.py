"""Speaking instead of typing: the route, the hint, and that nothing is kept."""
import os
from types import SimpleNamespace as NS

import pytest

from app.chat import listen


@pytest.fixture
def heard(monkeypatch):
    calls = []

    def fake(path, hint):
        calls.append({"path": path, "hint": hint, "existed": os.path.exists(path), "bytes": open(path, "rb").read()})
        return "Thanks for the cocoa, Dina."

    monkeypatch.setattr(listen, "_transcribe", fake)
    return calls


def post(client, session, data=b"audio bytes", name="clip.webm", kind="audio/webm"):
    return client.post(f"/api/sessions/{session}/transcribe", files={"file": (name, data, kind)})


def test_it_returns_the_words(client, session, heard):
    r = post(client, session)
    assert r.status_code == 200 and r.json() == {"text": "Thanks for the cocoa, Dina."}
    assert heard[0]["bytes"] == b"audio bytes"


def test_the_recording_is_not_kept(client, session, heard):
    post(client, session)
    assert heard[0]["existed"] and not os.path.exists(heard[0]["path"])


def test_the_recording_is_not_kept_after_a_failure(client, session, monkeypatch):
    seen = []

    def boom(path, hint):
        seen.append(path)
        raise listen.ListenUnavailable("no model")

    monkeypatch.setattr(listen, "_transcribe", boom)
    r = post(client, session)
    assert r.status_code == 503 and "no model" in r.json()["detail"]
    assert not os.path.exists(seen[0])


def test_nothing_is_added_to_the_chat(client, session, heard):
    before = client.get(f"/api/sessions/{session}").json()["messages"]
    post(client, session)
    assert client.get(f"/api/sessions/{session}").json()["messages"] == before


def test_the_file_name_cannot_choose_where_it_goes(client, session, heard):
    post(client, session, name="../../../../evil.sh")
    assert os.path.basename(heard[0]["path"]) != "evil.sh" and not heard[0]["path"].endswith(".sh")


def test_too_big_is_refused(client, session, heard, monkeypatch):
    monkeypatch.setattr(listen, "MAX_BYTES", 10)
    r = post(client, session, data=b"x" * 11)
    assert r.status_code == 413 and not heard


def test_empty_is_refused(client, session, heard):
    assert post(client, session, data=b"").status_code == 400


def test_missing_chat(client, heard):
    assert post(client, 999).status_code == 404


def test_the_hint_has_the_names_in_this_chat(client, llm, heard):
    wid = client.post("/api/worlds", json={"name": "Jackson", "entries": [
        {"name": "The Tipsy Bison", "keys": ["bison"], "content": "A bar."}]}).json()["id"]
    owner = client.post("/api/characters", json={"name": "Dina", "first_mes": "Hi.", "world_id": wid}).json()["id"]
    client.post("/api/characters", json={"name": "Joel", "first_mes": "Hi.", "world_id": wid})
    persona = client.post("/api/personas", json={"name": "Riley", "description": ""}).json()["id"]
    sid = client.post("/api/sessions", json={"character_id": owner, "persona_id": persona}).json()["id"]
    post(client, sid)
    assert heard[0]["hint"] == "Riley, Dina, Joel, The Tipsy Bison"


def test_a_long_hint_is_cut_with_the_likeliest_names_kept():
    world = NS(characters=[NS(name=f"Person{i}") for i in range(200)], entries=[])
    s = NS(persona=NS(name="Riley"), character=NS(id=1, name="Dina", world=world), character_id=1, members=[])
    hint = listen.hint_for(s)
    assert hint.startswith("Riley, Dina, Person0") and len(hint) <= listen.MAX_HINT


def test_missing_install_says_so(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_whisper(name, *a, **k):
        if name == "faster_whisper":
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(listen, "_model", None)
    monkeypatch.setattr(builtins, "__import__", no_whisper)
    with pytest.raises(listen.ListenUnavailable):
        listen._load()
