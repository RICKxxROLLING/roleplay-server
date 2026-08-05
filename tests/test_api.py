"""HTTP surface: routing, streaming, CRUD and settings persistence."""
from __future__ import annotations

import os
import subprocess
import sys

from conftest import TMP_DIR, sse_events


# --- routing / static -------------------------------------------------------

def test_api_lives_under_prefix(client):
    for path in ["/api/health", "/api/settings", "/api/characters", "/api/sessions"]:
        assert client.get(path).status_code == 200


def test_unprefixed_paths_do_not_hit_the_api(client):
    """Serving UI and API on one origin means a static path could otherwise
    shadow an endpoint. The /api prefix removes that whole class of bug."""
    body = client.get("/health").text
    assert '"ok"' not in body


def test_openapi_is_namespaced(client):
    assert client.get("/api/openapi.json").status_code == 200


# --- streaming --------------------------------------------------------------

def test_message_streams_tokens_and_persists(client, session):
    r = client.post(f"/api/sessions/{session}/messages", json={"content": "hello"})
    events = sse_events(r)
    assert [e["type"] for e in events][-1] == "done"
    assert any(e["type"] == "token" for e in events)

    msgs = client.get(f"/api/sessions/{session}").json()["messages"]
    assert [m["role"] for m in msgs][-2:] == ["user", "assistant"]


def test_speaker_prefix_stripped_before_persisting(client, session, llm):
    llm.reply = "Seraphine: *She nods.*"
    client.post(f"/api/sessions/{session}/messages", json={"content": "hi"})
    last = client.get(f"/api/sessions/{session}").json()["messages"][-1]["content"]
    assert not last.startswith("Seraphine:")


def test_empty_message_rejected(client, session):
    assert client.post(f"/api/sessions/{session}/messages", json={"content": "  "}).status_code == 400


def test_regenerate_replaces_last_reply(client, session):
    client.post(f"/api/sessions/{session}/messages", json={"content": "hi"})
    before = client.get(f"/api/sessions/{session}").json()["messages"]
    client.post(f"/api/sessions/{session}/regenerate")
    after = client.get(f"/api/sessions/{session}").json()["messages"]
    assert len(after) == len(before)
    assert after[-1]["content"] != before[-1]["content"]


# --- sessions ---------------------------------------------------------------

def test_greeting_index_selects_alternate(client, imported):
    r = client.post("/api/sessions", json={"character_id": imported, "greeting_index": 2})
    assert "Rain" in r.json()["messages"][0]["content"]


def test_greeting_placeholders_substituted(client, imported):
    p = client.post("/api/personas", json={"name": "Riley"}).json()["id"]
    r = client.post("/api/sessions", json={"character_id": imported, "persona_id": p})
    assert "Riley" in r.json()["messages"][0]["content"]
    assert "{{user}}" not in r.json()["messages"][0]["content"]


def test_rename_and_swap_persona(client, session):
    p = client.post("/api/personas", json={"name": "Wren"}).json()["id"]
    r = client.patch(f"/api/sessions/{session}", json={"title": "New name", "persona_id": p})
    assert r.json()["title"] == "New name"
    assert r.json()["persona_id"] == p

    cleared = client.patch(f"/api/sessions/{session}", json={"clear_persona": True})
    assert cleared.json()["persona_id"] is None


def test_empty_title_rejected(client, session):
    assert client.patch(f"/api/sessions/{session}", json={"title": " "}).status_code == 400


def test_message_edit_and_delete(client, session):
    client.post(f"/api/sessions/{session}/messages", json={"content": "who are you?"})
    msgs = client.get(f"/api/sessions/{session}").json()["messages"]
    mid = msgs[1]["id"]

    assert client.patch(
        f"/api/sessions/{session}/messages/{mid}", json={"content": "who are you really?"}
    ).json()["content"] == "who are you really?"

    client.delete(f"/api/sessions/{session}/messages/{mid}")
    assert len(client.get(f"/api/sessions/{session}").json()["messages"]) == len(msgs) - 1


def test_message_from_other_session_is_404(client, session, imported):
    other = client.post("/api/sessions", json={"character_id": imported}).json()["id"]
    mid = client.get(f"/api/sessions/{other}").json()["messages"][0]["id"]
    assert client.delete(f"/api/sessions/{session}/messages/{mid}").status_code == 404


# --- personas ---------------------------------------------------------------

def test_persona_crud(client):
    pid = client.post("/api/personas", json={"name": "Riley", "description": "a"}).json()["id"]
    assert client.patch(f"/api/personas/{pid}", json={"description": "b"}).json()["description"] == "b"
    assert client.delete(f"/api/personas/{pid}").json()["ok"]


def test_deleting_persona_detaches_sessions(client, imported):
    """Otherwise the session keeps a dangling FK and blows up on load."""
    pid = client.post("/api/personas", json={"name": "Riley"}).json()["id"]
    sid = client.post(
        "/api/sessions", json={"character_id": imported, "persona_id": pid}
    ).json()["id"]

    assert client.delete(f"/api/personas/{pid}").json()["chats_detached"] == 1
    assert client.get(f"/api/sessions/{sid}").status_code == 200


# --- models + settings ------------------------------------------------------

def test_model_list_and_pull_stream(client):
    body = client.get("/api/models").json()
    assert body["active"] == "test-model"
    assert body["supports_pull"] is True

    events = sse_events(client.post("/api/models/pull", json={"name": "test-model"}))
    assert events[-1]["type"] == "done"
    assert any(e["type"] == "progress" for e in events)


def test_health_reports_missing_model(client):
    client.patch("/api/settings", json={"model": "not-installed"})
    assert client.get("/api/health").json()["model_installed"] is False


def test_settings_write_to_database(client):
    client.patch("/api/settings", json={"temperature": 1.25})
    assert client.get("/api/settings").json()["temperature"] == 1.25

    from app.db import SessionLocal
    from app.db.models import AppSetting

    db = SessionLocal()
    row = db.get(AppSetting, "temperature")
    db.close()
    assert row is not None and row.value == 1.25


def test_settings_survive_a_real_restart(client):
    """A fresh process must pick the value back up -- reloading modules in-process
    would create a second settings object and prove nothing."""
    client.patch("/api/settings", json={"temperature": 1.25, "model": "other-model:8b"})

    code = (
        "from app.db import SessionLocal, init_db\n"
        "from app import settings_store\n"
        "from app.config import settings\n"
        "init_db()\n"
        "db = SessionLocal(); settings_store.load_into_settings(db); db.close()\n"
        "print(settings.temperature, settings.model)\n"
    )
    env = {**os.environ, "PYTHONPATH": os.getcwd()}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env
    )
    assert "1.25" in out.stdout and "other-model:8b" in out.stdout, out.stderr[-500:]


def test_unknown_setting_is_ignored(client):
    client.patch("/api/settings", json={"data_dir": "/etc"})
    from app.config import settings

    assert settings.data_dir == TMP_DIR


def test_repeat_window_reaches_the_backend(monkeypatch):
    """The bug this fixes was silent: Ollama defaults repeat_last_n to 64 --
    shorter than one reply here -- so the penalty could not see the previous
    turn and phrasing recycled while repeat_penalty looked correctly set.
    Nothing surfaces that unless the option is actually sent."""
    import httpx

    from app.llm.base import GenerationParams
    from app.llm.ollama import OllamaClient

    sent = {}

    class FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield '{"response": "hi", "done": true}'

    def fake_stream(self, method, url, **kw):
        sent.update(kw["json"])
        return FakeStream()

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)

    import asyncio

    client = OllamaClient("http://x", "m")

    async def run():
        async for _ in client.generate_stream("p", GenerationParams(repeat_last_n=1024)):
            pass

    asyncio.run(run())
    assert sent["options"]["repeat_last_n"] == 1024


def test_repeat_window_is_persistable(client):
    r = client.patch("/api/settings", json={"repeat_last_n": 512})
    assert r.status_code == 200, r.text
    assert r.json()["repeat_last_n"] == 512
    assert client.get("/api/settings").json()["repeat_last_n"] == 512
