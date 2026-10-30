"""Shared fixtures.

Environment must be set before `app.config` is imported anywhere, and pytest
imports conftest first -- hence the module-level os.environ writes.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
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
# The writing-first loop would fire real checks on a timer; tests call it directly.
os.environ["RP_AUTONOMY_SCHEDULER"] = "false"
# Some CI images export a SOCKS proxy that httpx can't construct a client for.
for var in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(var, None)

from fastapi.testclient import TestClient  # noqa: E402

from app.cards.models import CharacterCard  # noqa: E402
from app.llm.base import LLMClient  # noqa: E402
from app.llm.embeddings import Embedder  # noqa: E402

TMP_DIR = _TMP

# Wide enough that md5-bucket collisions between the few dozen distinct words in
# a test stay negligible -- a collision would give unrelated filler a spurious
# similarity score and make ranking assertions flaky.
EMBED_DIM = 512


class MockEmbedder(Embedder):
    """Bag-of-words vectors over a hashed vocabulary.

    Not a toy that returns noise: texts sharing words genuinely score higher
    than texts that don't, so retrieval tests can assert that the *right*
    message came back rather than merely that something did. md5 rather than
    hash() because Python randomises string hashing per process, and a test
    that reshuffles its own vocabulary every run is not a test.
    """

    def __init__(self):
        self.calls: list[list[str]] = []
        self.fail_with: Exception | None = None

    def vector(self, text: str) -> list[float]:
        vec = [0.0] * EMBED_DIM
        for word in re.findall(r"\w+", text.lower()):
            idx = int(hashlib.md5(word.encode()).hexdigest()[:8], 16) % EMBED_DIM
            vec[idx] += 1.0
        return vec

    async def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail_with:
            raise self.fail_with
        return [self.vector(t) for t in texts]


@pytest.fixture
def embedder(monkeypatch):
    """Install the mock everywhere a vector is requested."""
    import app.memory.rag as rag

    mock = MockEmbedder()
    monkeypatch.setattr(rag, "get_embedder", lambda: mock)
    return mock


def enable_retrieval(client, **overrides):
    """Turn retrieval on with test-friendly knobs.

    Thresholds are set explicitly rather than left at their defaults so the
    tests assert on retrieval *behaviour* and don't silently start failing when
    a default is retuned against a real embedding model.
    """
    body = {
        "retrieval_enabled": True,
        "retrieval_top_k": 3,
        # Sits between the ~0.24 that shared stopwords alone score against
        # MockEmbedder and the ~0.46 of a genuine topical match, with margin on
        # both sides -- so tests exercise the threshold rather than dodge it.
        "retrieval_min_score": 0.35,
        "retrieval_budget_tokens": 400,
        "retrieval_query_messages": 1,
    }
    body.update(overrides)
    r = client.patch("/api/settings", json=body)
    assert r.status_code == 200, r.text
    return r.json()


DRAFT_REPLY = """ Bram Halloway
Description: {{char}} keeps a brass ledger behind the bar of the Salt Lantern, a tavern wedged between two warehouses on the docks of Greyhaven. He is broad and grey-bearded, with ink on his fingers and a knife scar along one thumb, and he remembers every debt ever written in that ledger.
Personality: Gruff, patient, dry. Talks in short sentences and never raises his voice.
Scenario: {{user}} walks into the Salt Lantern on a stormy night owing {{char}} money.
First message: *Rain hammers the shutters of the Salt Lantern as {{char}} turns a page of the ledger, lamp oil smoking beside him.* "You're late, {{user}}." *He taps an entry with one ink-stained finger.*
Example dialogue: <START>
{{user}}: I'll pay you Friday.
{{char}}: "You said that last Friday." *He doesn't look up.*
<END>"""


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
        self.fact_calls: list[str] = []
        self.reply_count = 0
        #: Per-call replies, consumed first; `reply` once empty.
        self.replies: list[str] = []
        self.scene_calls: list[str] = []
        self.draft_calls: list[str] = []
        # A card draft as the model writes it, after the prefilled "Name:".
        self.draft_reply = DRAFT_REPLY
        self.draft_replies: list[str] = []
        # What a scene update returns, after the prefilled "Location:".
        self.scene_reply = " the flooded archive\nTime: midnight\nWeather: indoors\nPresent: Seraphine, You"

    async def generate_stream(self, prompt, params):
        self.prompts.append(prompt)
        if "you write character cards" in prompt.lower():
            self.draft_calls.append(prompt)
            yield self.draft_replies.pop(0) if self.draft_replies else self.draft_reply
            return
        if "track the scene" in prompt.lower():
            self.scene_calls.append(prompt)
            yield self.scene_reply
            return
        if "fact sheet" in prompt.lower():
            self.fact_calls.append(prompt)
            yield f"Riley found the ledger.\n- Seraphine keeps the archive key {len(self.fact_calls)}."
            return
        if "memory log" in prompt.lower():
            self.summary_calls.append(prompt)
            n = len(self.summary_calls)
            yield (
                f"FOLD#{n}: They searched the flooded archive together. "
                "A promise was made about the ledger. Unresolved: who set the fire."
            )
            return
        self.reply_count += 1
        # Report a prompt count the way Ollama does, when a test asks for one.
        ratio = getattr(self, "prompt_tokens_ratio", None)
        self.last_prompt_tokens = int(len(prompt) / ratio) if ratio else None
        reply = self.replies.pop(0) if self.replies else self.reply
        for piece in [f"{reply[:-1]} ", f"reply {self.reply_count}.*"]:
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

    from app.prompt import tokens

    tokens.reset()

    # Login throttling is in memory; one test's failures must not lock the next.
    from app.routes import auth as auth_routes

    auth_routes._reset_failures()

    fresh = Settings()
    for key, value in fresh.model_dump().items():
        setattr(settings, key, value)

    yield


@pytest.fixture
def llm(monkeypatch):
    """Install the mock across every module that resolves a client."""
    import app.cards.creator as creator
    import app.chat.autonomy as autonomy
    import app.chat.orchestrator as orch
    import app.memory.scene as scene
    import app.memory.summarizer as summarizer
    import app.routes.system as system

    mock = MockLLM()
    for module in (autonomy, creator, orch, scene, summarizer, system):
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
