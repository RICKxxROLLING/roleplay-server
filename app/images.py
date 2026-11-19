"""Pictures from a ComfyUI server on the owner's own network: scenes and portraits.

The 2080 can't hold an image model and the chat model at once, so pictures come from
another machine: the owner's PC runs ComfyUI and this server sends it a small txt2img graph
over the LAN. It stays inside the no-cloud rule (nothing leaves the house), and ComfyUI has
no password, so it should only ever listen on the home network. The browser never talks to
ComfyUI: pictures are fetched here, saved under the data folder and served from /api.

Every model call that writes the picture's prompt goes through the chat model first, then the
server judges it; nothing reaches ComfyUI for a scene with a character written as a minor.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import uuid

import httpx

from .cards.models import CharacterCard
from .config import settings
from .llm import GenerationParams, get_client
from .memory import trust
from .prompt.formats import get_format

#: How long one picture may take, end to end. A first run loads the checkpoint, which is slow.
TIMEOUT_S = 240
SCENE_SIZE = (1216, 832)
PORTRAIT_SIZE = (832, 1216)
MAX_PROMPT_CHARS = 600


def _http(timeout: float) -> httpx.AsyncClient:
    """One place that makes the client, so tests can hand in a fake ComfyUI."""
    return httpx.AsyncClient(timeout=timeout)


def _ws(url: str):
    """ComfyUI's live connection, where the finished picture arrives. One place, for tests."""
    from websockets.asyncio.client import connect

    return connect(url, max_size=64 * 1024 * 1024, open_timeout=10)


class ImagesUnavailable(RuntimeError):
    """ComfyUI can't be used right now, in words for the owner."""


#: ComfyUI's own node that sends the picture back over the live connection instead of writing
#: a file. Without it ComfyUI's SaveImage keeps every picture in its output folder, with the
#: prompt written into the PNG, so the owner's PC would hold a copy of every scene. The owner
#: asked that nothing of a chat stays on that PC, so no picture is made without this node.
PRIVATE_NODE = "SaveImageWebsocket"
_SAVE_ID = "7"
NOT_PRIVATE = ("ComfyUI on your PC can't send pictures back without saving them there, so no picture is "
               "made. Add ComfyUI's \"Save Image (Websocket)\" node: copy websocket_image_save.py into its "
               "custom_nodes folder and restart it (see docs/pictures.md).")


def _base() -> str:
    url = (settings.comfyui_url or "").strip().rstrip("/")
    if not url:
        raise ImagesUnavailable("No picture server is set. Add your ComfyUI address under Settings → Pictures.")
    return url


async def status() -> dict:
    """Whether ComfyUI answers, its checkpoints, and whether it can work without keeping files."""
    try:
        base = _base()
    except ImagesUnavailable as exc:
        return {"ok": False, "configured": False, "private": False, "error": str(exc), "checkpoints": []}
    try:
        async with _http(6) as http:
            r = await http.get(f"{base}/object_info/CheckpointLoaderSimple")
            r.raise_for_status()
            names = r.json()["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
            node = await http.get(f"{base}/object_info/{PRIVATE_NODE}")
            private = node.status_code == 200 and PRIVATE_NODE in (node.json() or {})
    except Exception as exc:  # anything at all: the PC is off, asleep, or not ComfyUI
        return {"ok": False, "configured": True, "private": False, "checkpoints": [],
                "error": f"ComfyUI didn't answer at {base}. Is the PC on and ComfyUI running? ({type(exc).__name__})"}
    return {"ok": private, "configured": True, "private": private, "checkpoints": list(names),
            "error": None if private else NOT_PRIVATE}


def graph(positive: str, negative: str, size: tuple[int, int], checkpoint: str, seed: int) -> dict:
    """ComfyUI's API-format graph for a plain txt2img, ending in the node that saves nothing."""
    w, h = size
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
            "seed": seed, "steps": settings.image_steps, "cfg": settings.image_cfg,
            "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        _SAVE_ID: {"class_type": PRIVATE_NODE, "inputs": {"images": ["6", 0]}},
    }


async def render(positive: str, size: tuple[int, int]) -> bytes:
    """One PNG from ComfyUI, or ImagesUnavailable with the reason.

    The picture comes back over the live connection and is never written on the PC; then the
    job is deleted from ComfyUI's history, the in-memory list of past prompts its /history
    serves, so the prompt text doesn't stay there either until ComfyUI restarts."""
    base = _base()
    found = await status()
    if not found["private"]:
        raise ImagesUnavailable(found["error"] or NOT_PRIVATE)
    checkpoint = (settings.comfyui_checkpoint or "").strip()
    if not checkpoint:
        if not found["checkpoints"]:
            raise ImagesUnavailable("ComfyUI has no checkpoints installed. Put a model in its models/checkpoints folder.")
        checkpoint = found["checkpoints"][0]
    client_id = uuid.uuid4().hex
    body = {"prompt": graph(positive, settings.image_negative, size, checkpoint, random.randrange(2**32)),
            "client_id": client_id}
    ws_url = re.sub(r"^http", "ws", base) + f"/ws?clientId={client_id}"
    try:
        async with _ws(ws_url) as ws, _http(30) as http:
            r = await http.post(f"{base}/prompt", json=body)
            if r.status_code >= 400:
                raise ImagesUnavailable(f"ComfyUI refused the picture: {r.text[:300]}")
            prompt_id = r.json()["prompt_id"]
            try:
                picture = await asyncio.wait_for(_collect(ws, prompt_id), TIMEOUT_S)
            finally:
                # Best effort: the prompt goes from ComfyUI's history whether or not it worked.
                try:
                    await http.post(f"{base}/history", json={"delete": [prompt_id]})
                except Exception:
                    pass
    except ImagesUnavailable:
        raise
    except asyncio.TimeoutError as exc:
        raise ImagesUnavailable("ComfyUI took too long. The first picture after starting it is the slowest; try again.") from exc
    except Exception as exc:
        raise ImagesUnavailable(f"Lost touch with ComfyUI at {base} ({type(exc).__name__}).") from exc
    if not picture:
        raise ImagesUnavailable("ComfyUI finished without sending a picture back.")
    return picture


async def _collect(ws, prompt_id: str) -> bytes:
    """The picture's bytes from the live connection, once ComfyUI says the job is done. Binary
    frames carry an 8-byte header (message type, image format); only those sent while the save
    node runs are the picture, as sampling previews arrive the same way."""
    current, picture = None, b""
    while True:
        msg = await ws.recv()
        if isinstance(msg, (bytes, bytearray)):
            if current == _SAVE_ID:
                picture += bytes(msg[8:])
            continue
        event = json.loads(msg)
        data = event.get("data") or {}
        if data.get("prompt_id") not in (None, prompt_id):
            continue
        if event.get("type") == "executing":
            current = data.get("node")
            if current is None and data.get("prompt_id") == prompt_id:
                return picture
        elif event.get("type") == "execution_error":
            raise ImagesUnavailable(f"ComfyUI hit an error: {data.get('exception_message', 'unknown')}"[:300])


def save(data: bytes) -> str:
    folder = os.path.join(settings.data_dir, "pictures")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{uuid.uuid4().hex}.png")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


# --- Writing the picture's prompt ------------------------------------------------------

SCENE_ASK = """{setting}

The people here:
{people}

Where and when: {where}

What just happened:
{transcript}

Write an image prompt for this moment, as one line of short comma-separated phrases: the place, \
the light and time of day, then each person by how they look and what they are doing. No names, \
no dialogue."""
PORTRAIT_ASK = """{setting}

About {name}: {about}

Write an image prompt for a portrait of {name}, as one line of short comma-separated phrases: \
age, build, face, hair, clothes, expression, and a simple background that suits them. No names."""

#: Words for a scene's style, kept out of what the model writes so it can't drift.
STYLE = "detailed digital painting, cinematic lighting, high quality"


def clean(raw: str, names: list[str]) -> str:
    """One line of phrases, names taken out (an image model doesn't know them), or ""."""
    line = raw.strip().split("\n")[0].strip(' "*')
    for n in sorted({p for full in names for p in (full, full.split()[0]) if p}, key=len, reverse=True):
        line = re.sub(rf"(?<!\w){re.escape(n)}(?!\w)", "a person", line, flags=re.I)
    line = re.sub(r"\s+", " ", line)[:MAX_PROMPT_CHARS].rstrip(", ")
    return line if len(line.split(",")) >= 3 else ""


async def _ask(body: str) -> str:
    fmt = get_format(settings.prompt_format)
    prompt = fmt.user.format(text=body) + fmt.joiner + fmt.response_open
    params = GenerationParams(temperature=0.6, top_p=0.9, top_k=40, repeat_penalty=1.05,
                              context_tokens=settings.context_tokens, max_new_tokens=160, stop=list(fmt.stops))
    return await get_client().generate(prompt, params)


def minors(characters) -> list[str]:
    """Names of anyone in the picture written as under 18. No picture is made of them."""
    return [c.name for c in characters
            if trust.is_minor(CharacterCard.model_validate(c.card or {"name": c.name}))]


async def scene_prompt(session, cast, user: str, recent: list[tuple[str, str]]) -> str:
    world = None
    from .chat import group

    world = group.world_of(session)
    scene = session.scene or {}
    where = ", ".join(str(scene[k]) for k in ("location", "time", "weather") if scene.get(k) and scene[k] != "unknown")
    people = "\n".join(f"- {c.name}: {' '.join(((c.card or {}).get('description') or '').split())[:400]}" for c in cast)
    persona = session.persona.description if session.persona else ""
    if persona:
        people += f"\n- {user}: {' '.join(persona.replace('{{user}}', user).split())[:400]}"
    body = SCENE_ASK.format(setting=f"Setting: {world.description[:600]}" if world and world.description else "",
                            people=people, where=where or "unknown",
                            transcript="\n".join(f"{who}: {text[:500]}" for who, text in recent[-4:]))
    for _ in range(2):
        line = clean(await _ask(body), [c.name for c in cast] + [user])
        if line:
            return f"{line}, {STYLE}"
    raise ImagesUnavailable("Couldn't describe this moment for a picture. Try again.")


async def portrait_prompt(character) -> str:
    card = character.card or {}
    world = character.world
    body = PORTRAIT_ASK.format(setting=f"Setting: {world.description[:600]}" if world and world.description else "",
                               name=character.name, about=" ".join((card.get("description") or "").split())[:700])
    for _ in range(2):
        line = clean(await _ask(body), [character.name])
        if line:
            return f"portrait, {line}, {STYLE}"
    raise ImagesUnavailable("Couldn't describe them for a portrait. Try again.")
