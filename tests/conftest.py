"""Shared fixtures.

Environment must be set before `app.config` is imported anywhere, and pytest
imports conftest first -- hence the module-level os.environ writes.
"""
from __future__ import annotations

import base64
import io
import json
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="roleplay-tests-")
os.environ["RP_DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["RP_DATA_DIR"] = _TMP
os.environ["RP_MODEL"] = "test-model"
os.environ["RP_TEMPERATURE"] = "0.9"
os.environ["RP_CONTEXT_TOKENS"] = "4096"
os.environ["RP_MAX_NEW_TOKENS"] = "400"
# Low fold threshold so memory tests trigger in a handful of turns rather than
# the ~40 the production default (1800) would need.
os.environ["RP_SUMMARY_TRIGGER_TOKENS"] = "300"
os.environ["RP_KEEP_RECENT_MESSAGES"] = "4"
# Some CI images export a SOCKS proxy that httpx can't construct a client for.
for var in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(var, None)

from fastapi.testclient import TestClient  # noqa: E402

from app.cards.models import CharacterCard  # noqa: E402
from app.llm.base import LLMClient  # noqa: E402

TMP_DIR = _TMP


class MockLLM(LLMClient):
    """Deterministic stand-in for a local model.

    Recognises the summarisation prompt by its header so memory tests can assert
    on folding without a real model.
    """

    supports_pull = True
    reply = "*She nods once.*"

    def __init__(self):
        self.prompts: list[str] = []
        self.summary_calls: list[str] = []
        self.reply_count = 0

    async def generate_stream(self, prompt, params):
        self.prompts.append(prompt)
        if "memory log" in prompt.lower():
            self.summary_calls.append(prompt)
            n = len(self.summary_calls)
            yield (
                f"FOLD#{n}: They searched the flooded archive together. "
                "A promise was made about the ledger. Unresolved: who set the fire."
            )
            return
        self.reply_count += 1
        for piece in [f"{self.reply[:-1]} ", f"reply {self.reply_count}.*"]:
            yield piece

    async def list_models(self):
        return ["test-model", "other-model:8b"]

    async def pull_model(self, name):
        for i in range(3):
            yield {"status": "downloading", "completed": (i + 1) * 100, "total": 300}
        yield {"status": "success", "completed": 300, "total": 300}


@pytest.fixture(autouse=True)
def _reset_state():
    """Fresh database and settings per test -- the settings object is a
    process-wide singleton that the settings API deliberately mutates."""
    from app.config import Settings, settings
    from app.db import engine
    from app.db.models import Base

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    fresh = Settings()
    for key, value in fresh.model_dump().items():
        setattr(settings, key, value)

    yield


@pytest.fixture
def llm(monkeypatch):
    """Install the mock across every module that resolves a client."""
    import app.chat.orchestrator as orch
    import app.memory.summarizer as summarizer
    import app.routes.system as system

    mock = MockLLM()
    for module in (orch, summarizer, system):
        monkeypatch.setattr(module, "get_client", lambda: mock)
    return mock


@pytest.fixture
def client(llm):
    from app.main import app

    with TestClient(app) as c:
        yield c


def card_png(**overrides) -> io.BytesIO:
    """Build a real V2 character card PNG in memory."""
    from PIL import Image, PngImagePlugin

    data = {
        "name": "Seraphine",
        "description": "{{char}} is a wandering archivist.",
        "personality": "Dry, guarded.",
        "scenario": "{{user}} meets {{char}} at midnight.",
        "first_mes": 'She looks up. "You are late, {{user}}."',
        "alternate_greetings": ["*The door creaks.*", "*Rain.*"],
        "tags": ["fantasy"],
    }
    data.update(overrides)
    payload = {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}

    img = Image.new("RGB", (32, 32), (40, 30, 60))
    meta = PngImagePlugin.PngInfo()
    meta.add_text("chara", base64.b64encode(json.dumps(payload).encode()).decode())
    buf = io.BytesIO()
    img.save(buf, "PNG", pnginfo=meta)
    buf.seek(0)
    return buf


@pytest.fixture
def imported(client):
    """A character imported through the real endpoint."""
    r = client.post(
        "/api/characters/import",
        files={"file": ("card.png", card_png(), "image/png")},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture
def session(client, imported):
    r = client.post("/api/sessions", json={"character_id": imported})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def make_card(entries=None, **kw) -> CharacterCard:
    return CharacterCard(name="Seraphine", character_book=entries or [], **kw)


def history(*texts) -> list[tuple[str, str]]:
    return [("user", t) for t in texts]


def sse_events(response) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
