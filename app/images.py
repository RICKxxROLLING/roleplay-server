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
POLL_S = 1.5
SCENE_SIZE = (1216, 832)
PORTRAIT_SIZE = (832, 1216)
MAX_PROMPT_CHARS = 600


def _http(timeout: float) -> httpx.AsyncClient:
    """One place that makes the client, so tests can hand in a fake ComfyUI."""
    return httpx.AsyncClient(timeout=timeout)


class ImagesUnavailable(RuntimeError):
    """ComfyUI can't be used right now, in words for the owner."""


def _base() -> str:
    url = (settings.comfyui_url or "").strip().rstrip("/")
    if not url:
        raise ImagesUnavailable("No picture server is set. Add your ComfyUI address under Settings → Pictures.")
    return url


async def status() -> dict:
    """Whether ComfyUI answers, and which checkpoints it has, for the Settings panel."""
    try:
        base = _base()
    except ImagesUnavailable as exc:
        return {"ok": False, "configured": False, "error": str(exc), "checkpoints": []}
    try:
        async with _http(6) as http:
            r = await http.get(f"{base}/object_info/CheckpointLoaderSimple")
            r.raise_for_status()
            names = r.json()["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
    except Exception as exc:  # anything at all: the PC is off, asleep, or not ComfyUI
        return {"ok": False, "configured": True, "checkpoints": [],
                "error": f"ComfyUI didn't answer at {base}. Is the PC on and ComfyUI running? ({type(exc).__name__})"}
    return {"ok": True, "configured": True, "checkpoints": list(names), "error": None}


def graph(positive: str, negative: str, size: tuple[int, int], checkpoint: str, seed: int) -> dict:
    """ComfyUI's API-format graph for a plain txt2img: the nodes every install has."""
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
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "roleplay"}},
    }


async def render(positive: str, size: tuple[int, int]) -> bytes:
    """One PNG from ComfyUI, or ImagesUnavailable with the reason."""
    base = _base()
    checkpoint = (settings.comfyui_checkpoint or "").strip()
    if not checkpoint:
        found = await status()
        if not found["ok"]:
            raise ImagesUnavailable(found["error"])
        if not found["checkpoints"]:
            raise ImagesUnavailable("ComfyUI has no checkpoints installed. Put a model in its models/checkpoints folder.")
        checkpoint = found["checkpoints"][0]
    body = {"prompt": graph(positive, settings.image_negative, size, checkpoint, random.randrange(2**32)),
            "client_id": uuid.uuid4().hex}
    try:
        async with _http(30) as http:
            r = await http.post(f"{base}/prompt", json=body)
            if r.status_code >= 400:
                raise ImagesUnavailable(f"ComfyUI refused the picture: {r.text[:300]}")
            prompt_id = r.json()["prompt_id"]
            waited = 0.0
            while waited < TIMEOUT_S:
                await asyncio.sleep(POLL_S)
                waited += POLL_S
                h = (await http.get(f"{base}/history/{prompt_id}")).json()
                done = h.get(prompt_id)
                if not done:
                    continue
                for out in (done.get("outputs") or {}).values():
                    for img in out.get("images") or []:
                        pic = await http.get(f"{base}/view", params={
                            "filename": img["filename"], "subfolder": img.get("subfolder", ""),
                            "type": img.get("type", "output")})
                        pic.raise_for_status()
                        return pic.content
                messages = (done.get("status") or {}).get("messages") or []
                raise ImagesUnavailable(f"ComfyUI finished without a picture. {messages[-1] if messages else ''}".strip())
    except ImagesUnavailable:
        raise
    except Exception as exc:
        raise ImagesUnavailable(f"Lost touch with ComfyUI at {base} ({type(exc).__name__}).") from exc
    raise ImagesUnavailable("ComfyUI took too long. The first picture after starting it is the slowest; try again.")


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
