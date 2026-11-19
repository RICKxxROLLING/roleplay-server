"""Pictures from ComfyUI, against a fake ComfyUI that speaks its HTTP API."""
from __future__ import annotations

import json

import httpx
import pytest
from conftest import sse_events

from app import images

PNG = b"\x89PNG\r\n\x1a\nfake"


class FakeComfy:
    def __init__(self):
        self.prompts: list[dict] = []
        self.up = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("down")
        path = request.url.path
        if path == "/object_info/CheckpointLoaderSimple":
            return httpx.Response(200, json={"CheckpointLoaderSimple": {"input": {"required": {
                "ckpt_name": [["sdxl_base.safetensors", "juggernaut.safetensors"], {}]}}}})
        if path == "/prompt":
            self.prompts.append(json.loads(request.content))
            return httpx.Response(200, json={"prompt_id": "p1", "number": 0})
        if path == "/history/p1":
            return httpx.Response(200, json={"p1": {"outputs": {"7": {"images": [
                {"filename": "roleplay_0001.png", "subfolder": "", "type": "output"}]}}}})
        if path == "/view":
            assert request.url.params["filename"] == "roleplay_0001.png"
            return httpx.Response(200, content=PNG)
        return httpx.Response(404)


@pytest.fixture
def comfy(client, monkeypatch):
    fake = FakeComfy()
    monkeypatch.setattr(images, "_http",
                        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), timeout=timeout))
    monkeypatch.setattr(images, "POLL_S", 0)
    client.patch("/api/settings", json={"comfyui_url": "http://10.2.1.123:8188/"})
    return fake


@pytest.fixture
def describes(llm, monkeypatch):
    real = llm.generate

    async def gen(prompt, params):
        if "Write an image prompt" in prompt:
            return " a general store, morning light, Seraphine at the counter smiling, shelves of jars\nmore"
        return await real(prompt, params)

    monkeypatch.setattr(llm, "generate", gen, raising=False)


def test_status_says_when_nothing_is_set_up(client):
    got = client.get("/api/pictures/status").json()
    assert not got["ok"] and not got["configured"] and "Settings" in got["error"]


def test_status_lists_the_checkpoints(client, comfy):
    got = client.get("/api/pictures/status").json()
    assert got["ok"] and got["checkpoints"] == ["sdxl_base.safetensors", "juggernaut.safetensors"]


def test_a_picture_of_this_moment(client, session, llm, comfy, describes):
    sse_events(client.post(f"/api/sessions/{session}/messages", json={"content": "Hello."}))
    r = client.post(f"/api/sessions/{session}/pictures")
    assert r.status_code == 200, r.text
    pic = r.json()
    # Names out (an image model doesn't know them), the first line only, the house style on.
    assert "Seraphine" not in pic["prompt"] and "a person at the counter" in pic["prompt"]
    assert "more" not in pic["prompt"] and pic["prompt"].endswith(images.STYLE)
    sent = comfy.prompts[0]["prompt"]
    assert sent["1"]["inputs"]["ckpt_name"] == "sdxl_base.safetensors"
    assert sent["4"]["inputs"]["width"] == 1216 and "nsfw" in sent["3"]["inputs"]["text"]
    assert client.get(pic["url"]).content == PNG
    got = client.get(f"/api/sessions/{session}").json()
    assert got["pictures"][0]["after_message_id"] == got["messages"][-1]["id"]
    # Never part of a prompt.
    assert all("image prompt" not in p.lower() or "Write an image prompt" in p for p in llm.prompts)
    assert client.delete(pic["url"]).status_code == 200
    assert client.get(f"/api/sessions/{session}").json()["pictures"] == []


def test_the_chosen_checkpoint_is_used(client, session, comfy, describes):
    client.patch("/api/settings", json={"comfyui_checkpoint": "juggernaut.safetensors"})
    client.post(f"/api/sessions/{session}/pictures")
    assert comfy.prompts[0]["prompt"]["1"]["inputs"]["ckpt_name"] == "juggernaut.safetensors"


def test_a_pc_that_is_off_says_so(client, session, comfy, describes):
    comfy.up = False
    r = client.post(f"/api/sessions/{session}/pictures")
    assert r.status_code == 503 and "Is the PC on" in r.json()["detail"]


def test_nobody_written_as_a_minor_is_pictured(client, comfy, describes):
    kid = client.post("/api/characters", json={"name": "Lily", "description": "Lily is a 12-year-old girl."}).json()["id"]
    sid = client.post("/api/sessions", json={"character_id": kid}).json()["id"]
    r = client.post(f"/api/sessions/{sid}/pictures")
    assert r.status_code == 400 and "under 18" in r.json()["detail"]
    assert client.post(f"/api/characters/{kid}/portrait").status_code == 400
    assert comfy.prompts == []


def test_a_portrait_becomes_the_avatar(client, comfy, llm, monkeypatch):
    cid = client.post("/api/characters", json={"name": "Hana", "description": "Hana, 54, keeps the store."}).json()["id"]
    real = llm.generate

    async def gen(prompt, params):
        if "portrait of Hana" in prompt:
            return " woman in her fifties, grey braid, reading glasses, shop apron, shelves behind"
        return await real(prompt, params)

    monkeypatch.setattr(llm, "generate", gen, raising=False)
    r = client.post(f"/api/characters/{cid}/portrait")
    assert r.status_code == 200, r.text
    assert comfy.prompts[0]["prompt"]["4"]["inputs"]["height"] == 1216
    assert client.get(f"/api/characters/{cid}/avatar").content == PNG
    assert next(c for c in client.get("/api/characters").json() if c["id"] == cid)["has_avatar"]


def test_a_useless_description_is_refused(client, session, comfy, llm, monkeypatch):
    real = llm.generate

    async def gen(prompt, params):
        if "Write an image prompt" in prompt:
            return " She smiles."
        return await real(prompt, params)

    monkeypatch.setattr(llm, "generate", gen, raising=False)
    r = client.post(f"/api/sessions/{session}/pictures")
    assert r.status_code == 502 and comfy.prompts == []


def test_deleting_a_chat_deletes_its_pictures(client, session, comfy, describes):
    pic = client.post(f"/api/sessions/{session}/pictures").json()
    client.delete(f"/api/sessions/{session}")
    assert client.get(pic["url"]).status_code == 404
