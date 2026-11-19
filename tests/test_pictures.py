"""Pictures from ComfyUI, against a fake ComfyUI that speaks its HTTP API and live connection.

The owner asked that nothing of a chat is kept on the PC running ComfyUI, so these tests also
pin that the picture arrives over the connection (no SaveImage, no /view of a saved file), that
each job is deleted from ComfyUI's history, and that an install that can't do this is refused.
"""
from __future__ import annotations

import json

import httpx
import pytest

from conftest import sse_events

from app import images

PNG = b"\x89PNG\r\n\x1a\nfake"
HEADER = (2).to_bytes(4, "big") + (2).to_bytes(4, "big")


class FakeComfy:
    def __init__(self):
        self.prompts: list[dict] = []
        self.deleted: list[str] = []
        self.up = True
        self.private = True
        self.ws_urls: list[str] = []
        self.views = 0
        self.starts = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.port == 8190:  # the helper on the PC (deploy/comfy-waker)
            if path == "/start":
                self.starts += 1
                self.up = True
                return httpx.Response(200, json={"comfyui": "starting"})
            return httpx.Response(200, json={"comfyui": "running" if self.up else "stopped"})
        if not self.up:
            raise httpx.ConnectError("down")
        if path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if path == "/object_info/CheckpointLoaderSimple":
            return httpx.Response(200, json={"CheckpointLoaderSimple": {"input": {"required": {
                "ckpt_name": [["sdxl_base.safetensors", "juggernaut.safetensors"], {}]}}}})
        if path == "/object_info/SaveImageWebsocket":
            return httpx.Response(200, json={"SaveImageWebsocket": {}} if self.private else {})
        if path == "/prompt":
            self.prompts.append(json.loads(request.content))
            return httpx.Response(200, json={"prompt_id": "p1", "number": 0})
        if path == "/history" and request.method == "POST":
            self.deleted += json.loads(request.content).get("delete", [])
            return httpx.Response(200)
        if path == "/view":
            self.views += 1
        return httpx.Response(404)

    def socket(self, url: str):
        self.ws_urls.append(url)
        comfy = self

        class Socket:
            async def __aenter__(self):
                # What ComfyUI sends: a preview while sampling, then the picture from the save node.
                self.frames = [
                    json.dumps({"type": "executing", "data": {"node": "5", "prompt_id": "p1"}}),
                    HEADER + b"a latent preview",
                    json.dumps({"type": "executing", "data": {"node": "7", "prompt_id": "p1"}}),
                    HEADER + PNG,
                    json.dumps({"type": "executing", "data": {"node": None, "prompt_id": "p1"}}),
                ]
                return self

            async def __aexit__(self, *exc):
                return False

            async def recv(self):
                assert comfy.prompts, "the picture was read before the job was queued"
                return self.frames.pop(0)

        return Socket()


@pytest.fixture
def comfy(client, monkeypatch):
    fake = FakeComfy()
    monkeypatch.setattr(images, "_http",
                        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), timeout=timeout))
    monkeypatch.setattr(images, "_ws", fake.socket)
    client.patch("/api/settings", json={"comfyui_url": "http://10.2.1.123:8188/"})
    return fake


@pytest.fixture
def describes(llm, monkeypatch):
    real = llm.generate

    count = {"looks": 0}

    async def gen(prompt, params):
        if "Describe how" in prompt and "looks" in prompt:
            count["looks"] += 1
            return " a woman, tall, olive skin, long dark hair in a braid, green eyes, linen apron, silver ring"
        if "Describe the moment" in prompt:
            count["moment"] = prompt
            return (" a general store, shelves of jars\nLight: morning light\n"
                    "Seraphine: at the counter, smiling as Seraphine wipes it down\nmore")
        return await real(prompt, params)

    monkeypatch.setattr(llm, "generate", gen, raising=False)
    return count


def test_status_says_when_nothing_is_set_up(client):
    got = client.get("/api/pictures/status").json()
    assert not got["ok"] and not got["configured"] and "Settings" in got["error"]


def test_status_lists_the_checkpoints(client, comfy):
    got = client.get("/api/pictures/status").json()
    assert got["ok"] and got["private"]
    assert got["checkpoints"] == ["sdxl_base.safetensors", "juggernaut.safetensors"]


def test_a_picture_of_this_moment_leaves_nothing_on_the_pc(client, session, llm, comfy, describes):
    sse_events(client.post(f"/api/sessions/{session}/messages", json={"content": "Hello."}))
    r = client.post(f"/api/sessions/{session}/pictures")
    assert r.status_code == 200, r.text
    pic = r.json()
    # Place and light, then the person as their saved look and what they're doing; names out
    # (an image model doesn't know them) and the house style on.
    assert pic["prompt"].startswith("a general store, shelves of jars, morning light, a woman, tall, olive skin")
    assert "silver ring, at the counter, smiling as a woman wipes it down" in pic["prompt"]
    assert "Seraphine" not in pic["prompt"] and "more" not in pic["prompt"] and pic["prompt"].endswith(images.STYLE)
    sent = comfy.prompts[0]["prompt"]
    assert sent["1"]["inputs"]["ckpt_name"] == "sdxl_base.safetensors"
    assert sent["4"]["inputs"]["width"] == 1216 and "nsfw" in sent["3"]["inputs"]["text"]
    # Nothing saved on the PC: no SaveImage node, no file fetched, and the job's history removed.
    assert not any(n["class_type"] in ("SaveImage", "PreviewImage") for n in sent.values())
    assert sent["7"]["class_type"] == "SaveImageWebsocket"
    assert comfy.views == 0 and comfy.deleted == ["p1"]
    assert comfy.ws_urls == [f"ws://10.2.1.123:8188/ws?clientId={comfy.prompts[0]['client_id']}"]
    # The picture is the save node's frame, not the sampling preview.
    assert client.get(pic["url"]).content == PNG
    got = client.get(f"/api/sessions/{session}").json()
    assert got["pictures"][0]["after_message_id"] == got["messages"][-1]["id"]
    assert client.delete(pic["url"]).status_code == 200
    assert client.get(f"/api/sessions/{session}").json()["pictures"] == []


def test_an_install_that_would_keep_files_is_refused(client, session, comfy, describes):
    comfy.private = False
    got = client.get("/api/pictures/status").json()
    assert not got["ok"] and not got["private"] and "Save Image (Websocket)" in got["error"]
    r = client.post(f"/api/sessions/{session}/pictures")
    assert r.status_code == 503 and "custom_nodes" in r.json()["detail"]
    assert comfy.prompts == []


def test_the_detail_pass_feeds_the_save_node_and_can_be_turned_off(client, session, comfy, describes):
    # On by default: the picture is scaled up, sampled again lightly, and that is what's sent back.
    client.post(f"/api/sessions/{session}/pictures")
    sent = comfy.prompts[0]["prompt"]
    assert sent["8"]["inputs"]["scale_by"] == images.DETAIL_SCALE
    assert sent["10"]["inputs"]["denoise"] == images.DETAIL_DENOISE and sent["10"]["inputs"]["seed"] == sent["5"]["inputs"]["seed"]
    assert sent["7"]["inputs"]["images"] == ["11", 0]
    assert "asymmetrical eyes" in sent["3"]["inputs"]["text"]
    # Still nothing that writes a file, with the extra nodes.
    assert {n["class_type"] for n in sent.values()} & {"SaveImage", "PreviewImage"} == set()
    client.patch("/api/settings", json={"image_detail": False})
    client.post(f"/api/sessions/{session}/pictures")
    plain = comfy.prompts[1]["prompt"]
    assert "8" not in plain and plain["7"]["inputs"]["images"] == ["6", 0]


def test_the_chosen_checkpoint_is_used(client, session, comfy, describes):
    client.patch("/api/settings", json={"comfyui_checkpoint": "juggernaut.safetensors"})
    client.post(f"/api/sessions/{session}/pictures")
    assert comfy.prompts[0]["prompt"]["1"]["inputs"]["ckpt_name"] == "juggernaut.safetensors"


def test_a_stopped_comfyui_is_started_by_the_helper_for_a_picture(client, session, comfy, describes):
    client.get("/api/pictures/status")  # seen once while running: its models are remembered
    comfy.up = False
    client.patch("/api/settings", json={"comfyui_waker_url": "http://10.2.1.123:8190"})
    got = client.get("/api/pictures/status").json()
    # Opening Settings doesn't start it; it says it's stopped and still offers the models.
    assert got["asleep"] and "starts by itself" in got["error"] and comfy.starts == 0
    assert got["checkpoints"] == ["sdxl_base.safetensors", "juggernaut.safetensors"]
    r = client.post(f"/api/sessions/{session}/pictures")
    assert r.status_code == 200, r.text
    assert comfy.starts == 1 and comfy.deleted == ["p1"]


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


def test_names_become_a_man_or_a_woman():
    # Live: Marcus, with one "He" in his card, was "a person" and drawn as a woman.
    marcus = ("Marcus Bell is a 35-year-old high school science teacher. Tall, rumpled cardigan. "
              "He moved back to Maple Hollow to look after his dad.")
    assert images.who(marcus) == "a man"
    assert images.who("Sadie Wren is a 24-year-old barista. Freckles, overalls.") == "a person"
    assert images.who("*She glances up at Riley and sets down her brush.*") == "a woman"
    assert images.clean("a shop, Marcus holding coffee, Riley at the door, rain", {"Marcus Bell": "a man", "Riley": "a man"}) \
        == "a shop, a man holding coffee, a man at the door, rain"


def test_a_portrait_says_who_it_is(client, comfy, llm, monkeypatch):
    cid = client.post("/api/characters", json={"name": "Marcus Bell", "description":
                      "Marcus, 35, teaches science. He coaches soccer and looks after his dad."}).json()["id"]
    real = llm.generate

    async def gen(prompt, params):
        if "Describe how Marcus Bell looks" in prompt:
            assert "Marcus Bell is 35 years old." in prompt
            return " a man, late 20s, tall, rumpled cardigan, dark hair, stubble, brown eyes"
        if "portrait of Marcus" in prompt:
            return " warm smile, leaning on a desk, classroom behind"
        return await real(prompt, params)

    monkeypatch.setattr(llm, "generate", gen, raising=False)
    assert client.post(f"/api/characters/{cid}/portrait").status_code == 200
    # The card's age is kept even when the model guesses another, and the look is saved.
    text = comfy.prompts[0]["prompt"]["2"]["inputs"]["text"]
    assert text.startswith("portrait of a man, 35 years old, late 20s, tall") and "warm smile, leaning" in text
    assert client.get(f"/api/characters/{cid}").json()["look"].startswith("a man, 35 years old")


def test_a_look_is_written_once_kept_and_editable(client, session, comfy, describes):
    client.post(f"/api/sessions/{session}/pictures")
    client.post(f"/api/sessions/{session}/pictures")
    assert describes["looks"] == 1  # the character's; the test chat has no persona
    first, second = (p["prompt"]["2"]["inputs"]["text"] for p in comfy.prompts)
    assert "long dark hair in a braid, green eyes" in first and "long dark hair in a braid, green eyes" in second
    cid = client.get(f"/api/sessions/{session}").json()["character_id"]
    client.patch(f"/api/characters/{cid}", json={"look": "a woman, short red hair, blue coat, freckles"})
    client.post(f"/api/sessions/{session}/pictures")
    assert "a woman, short red hair, blue coat, freckles, at the counter" in comfy.prompts[2]["prompt"]["2"]["inputs"]["text"]
    assert describes["looks"] == 1
    # Cleared, it is written afresh.
    client.patch(f"/api/characters/{cid}", json={"look": ""})
    client.post(f"/api/sessions/{session}/pictures")
    assert describes["looks"] == 2


def test_the_latest_reply_is_read_whole(client, session, llm, comfy, describes):
    long = "She talks about the harvest. " * 60 + "PLOTWORD at the very end."
    llm.replies = [long]
    sse_events(client.post(f"/api/sessions/{session}/messages", json={"content": "Tell me everything."}))
    client.post(f"/api/sessions/{session}/pictures")
    asked = describes["moment"]
    assert "PLOTWORD at the very end" in asked and "Tell me everything" in asked


def test_your_persona_gets_a_look_too(client, comfy, describes):
    pid = client.post("/api/personas", json={"name": "Riley", "description": "Riley is a quiet man. He fixes radios."}).json()["id"]
    cid = client.post("/api/characters", json={"name": "Seraphine", "description": "Seraphine, 40, keeps the shop. She hums."}).json()["id"]
    sid = client.post("/api/sessions", json={"character_id": cid, "persona_id": pid}).json()["id"]
    sse_events(client.post(f"/api/sessions/{sid}/messages", json={"content": "Hello."}))
    assert client.post(f"/api/sessions/{sid}/pictures").status_code == 200
    assert describes["looks"] == 2
    assert client.get("/api/personas").json()[0]["look"]


def test_two_people_each_get_their_own_part_of_the_picture(client, comfy, llm, monkeypatch):
    pid = client.post("/api/personas", json={"name": "Riley", "description": "Riley is a quiet man. He fixes radios."}).json()["id"]
    cid = client.post("/api/characters", json={"name": "Seraphine", "description": "Seraphine, 40, keeps the shop. She hums."}).json()["id"]
    sid = client.post("/api/sessions", json={"character_id": cid, "persona_id": pid}).json()["id"]
    sse_events(client.post(f"/api/sessions/{sid}/messages", json={"content": "Hello."}))
    real = llm.generate

    async def gen(prompt, params):
        if "Describe how Seraphine looks" in prompt:
            return " a woman, red hair in a bun, nose ring, denim overalls, freckles"
        if "Describe how Riley looks" in prompt:
            return " a man, short brown hair, glasses, chambray shirt, silver watch"
        if "Describe the moment" in prompt:
            return " a small shop\nLight: grey rain light\nSeraphine: behind the counter, smiling\nRiley: at the counter, holding a mug"
        return await real(prompt, params)

    monkeypatch.setattr(llm, "generate", gen, raising=False)
    assert client.post(f"/api/sessions/{sid}/pictures").status_code == 200
    g = comfy.prompts[0]["prompt"]
    assert g["2"]["inputs"]["text"].startswith("two people, a woman and a man, a small shop, grey rain light")
    assert "glasses" not in g["2"]["inputs"]["text"]
    areas = [n["inputs"] for n in g.values() if n["class_type"] == "ConditioningSetAreaPercentage"]
    assert [(a["x"], a["width"]) for a in areas] == [(0.0, 0.6), (0.45, 0.55)]
    assert "nose ring" in g["p0"]["inputs"]["text"] and "glasses" not in g["p0"]["inputs"]["text"]
    assert "glasses" in g["p1"]["inputs"]["text"] and "nose ring" not in g["p1"]["inputs"]["text"]
    # Both passes draw with the areas, and the one-line prompt is what's kept with the picture.
    assert g["5"]["inputs"]["positive"] == g["10"]["inputs"]["positive"] == ["c1", 0]
    got = client.get(f"/api/sessions/{sid}").json()["pictures"][0]
    assert "nose ring" in got["prompt"] and "glasses" in got["prompt"]


def test_a_useless_description_is_refused(client, session, comfy, llm, monkeypatch):
    real = llm.generate

    async def gen(prompt, params):
        if "Describe the moment" in prompt:
            return " She smiles."
        return await real(prompt, params)

    monkeypatch.setattr(llm, "generate", gen, raising=False)
    r = client.post(f"/api/sessions/{session}/pictures")
    assert r.status_code == 502 and comfy.prompts == []


def test_deleting_a_chat_deletes_its_pictures(client, session, comfy, describes):
    pic = client.post(f"/api/sessions/{session}/pictures").json()
    client.delete(f"/api/sessions/{session}")
    assert client.get(pic["url"]).status_code == 404
