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


#: How long a stopped ComfyUI may take to answer once the helper starts it. 17 s on the owner's PC.
WAKE_S = 120
#: The models ComfyUI last listed, so Settings can still offer them while it's stopped.
_known: list[str] = []


def _waker() -> str:
    return (settings.comfyui_waker_url or "").strip().rstrip("/")


async def _waker_says() -> str:
    """The helper's word on ComfyUI ("running", "starting", "stopped"), or "" if it doesn't answer."""
    if not _waker():
        return ""
    try:
        async with _http(4) as http:
            r = await http.get(f"{_waker()}/status")
            return r.json().get("comfyui", "") if r.status_code == 200 else ""
    except Exception:
        return ""


async def wake() -> None:
    """Ask the helper on the PC (deploy/comfy-waker) to start ComfyUI, and wait until it answers.
    Also counts as activity, so it isn't stopped between two pictures."""
    if not _waker():
        return
    try:
        async with _http(6) as http:
            await http.post(f"{_waker()}/start")
    except Exception as exc:
        raise ImagesUnavailable(f"The helper on your PC didn't answer at {_waker()}. Is the PC on? "
                                f"({type(exc).__name__})") from exc
    deadline = asyncio.get_event_loop().time() + WAKE_S
    while asyncio.get_event_loop().time() < deadline:
        if await _probe():
            return  # Answering; `status` then says whether it can work privately.
        await asyncio.sleep(2)
    raise ImagesUnavailable("ComfyUI didn't start on your PC in time. Try again in a moment.")


async def _probe() -> bool:
    """ComfyUI answers at all, private node or not."""
    try:
        async with _http(3) as http:
            return (await http.get(f"{_base()}/queue")).status_code == 200
    except Exception:
        return False


async def status() -> dict:
    """Whether ComfyUI answers, its checkpoints, and whether it can work without keeping files."""
    global _known
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
        if await _waker_says():
            # Stopped on purpose: it starts again when a picture is asked for.
            return {"ok": False, "configured": True, "private": False, "asleep": True, "checkpoints": _known,
                    "error": "ComfyUI is stopped on your PC to keep its graphics card free. It starts by itself "
                             "when you ask for a picture, which makes that first one slower."}
        return {"ok": False, "configured": True, "private": False, "checkpoints": [],
                "error": f"ComfyUI didn't answer at {base}. Is the PC on and ComfyUI running? ({type(exc).__name__})"}
    _known = list(names)
    return {"ok": private, "configured": True, "private": private, "checkpoints": list(names),
            "error": None if private else NOT_PRIVATE}


#: The detail pass: the first picture scaled up 1.5x and sampled again lightly. At 1216x832 a
#: face is a small part of the frame, and SDXL draws small eyes badly (uneven, smeared). Same
#: scene, same seeds, Juggernaut XL: eyes came out even and sharp with it, and the picture kept
#: its layout at this strength. 14 s became 35 s on the owner's RX 9060 XT.
DETAIL_SCALE = 1.5
DETAIL_DENOISE = 0.4
DETAIL_STEPS = 20


def graph(positive: str, negative: str, size: tuple[int, int], checkpoint: str, seed: int) -> dict:
    """ComfyUI's API-format graph for a txt2img, with the detail pass when it's on, ending in
    the node that saves nothing."""
    w, h = size
    g = {
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
    if settings.image_detail:
        g["8"] = {"class_type": "ImageScaleBy", "inputs": {"image": ["6", 0], "upscale_method": "lanczos",
                                                            "scale_by": DETAIL_SCALE}}
        g["9"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["8", 0], "vae": ["1", 2]}}
        g["10"] = {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["9", 0],
            "seed": seed, "steps": DETAIL_STEPS, "cfg": settings.image_cfg,
            "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": DETAIL_DENOISE}}
        g["11"] = {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["1", 2]}}
        g[_SAVE_ID]["inputs"]["images"] = ["11", 0]
    return g


async def render(positive: str, size: tuple[int, int]) -> bytes:
    """One PNG from ComfyUI, or ImagesUnavailable with the reason.

    The picture comes back over the live connection and is never written on the PC; then the
    job is deleted from ComfyUI's history, the in-memory list of past prompts its /history
    serves, so the prompt text doesn't stay there either until ComfyUI restarts."""
    base = _base()
    await wake()
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


_HE = re.compile(r"\b(?:he|him|his|himself|man|boy|guy|father|son|husband|brother)\b", re.I)
_SHE = re.compile(r"\b(?:she|her|hers|herself|woman|girl|lady|mother|daughter|wife|sister)\b", re.I)


def who(text: str) -> str:
    """"a man", "a woman" or "a person", from how a card or persona speaks of them. Names became
    "a person" at first, and Marcus, a 35-year-old teacher, was drawn as a woman."""
    he, she = len(_HE.findall(text or "")), len(_SHE.findall(text or ""))
    if he and not she or he >= 3 * max(she, 1):
        return "a man"
    if she and not he or she >= 3 * max(he, 1):
        return "a woman"
    return "a person"


def clean(raw: str, names: list[str] | dict[str, str]) -> str:
    """One line of phrases, names taken out (an image model doesn't know them), or "". `names`
    may map each name to what stands in for it ("a man"); a plain list uses "a person"."""
    line = raw.strip().split("\n")[0].strip(' "*')
    stand_in = names if isinstance(names, dict) else {n: "a person" for n in names}
    parts = {p: stand_in[full] for full in stand_in for p in (full, full.split()[0]) if p}
    for n in sorted(parts, key=len, reverse=True):
        line = re.sub(rf"(?<!\w){re.escape(n)}(?!\w)", parts[n], line, flags=re.I)
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
    about = {c.name: " ".join(((c.card or {}).get(k) or "") for k in ("description", "personality")) for c in cast}
    stand_in = {}
    for c in cast:
        # The card first, greeting included; else how their own replies speak of them ("She
        # glances up"), as Sadie's card never says.
        stand_in[c.name] = who(about[c.name] + " " + ((c.card or {}).get("first_mes") or ""))
        if stand_in[c.name] == "a person":
            stand_in[c.name] = who(" ".join(text for name, text in recent if name == c.name))
    people = "\n".join(f"- {c.name}, {stand_in[c.name]}: {' '.join(about[c.name].split())[:400]}" for c in cast)
    persona = session.persona.description if session.persona else ""
    if persona:
        stand_in[user] = who(persona)
        people += f"\n- {user}, {stand_in[user]}: {' '.join(persona.replace('{{user}}', user).split())[:400]}"
    body = SCENE_ASK.format(setting=f"Setting: {world.description[:600]}" if world and world.description else "",
                            people=people, where=where or "unknown",
                            transcript="\n".join(f"{name}: {text[:500]}" for name, text in recent[-4:]))
    for _ in range(2):
        line = clean(await _ask(body), stand_in)
        if line:
            return f"{line}, {STYLE}"
    raise ImagesUnavailable("Couldn't describe this moment for a picture. Try again.")


async def portrait_prompt(character) -> str:
    card = character.card or {}
    world = character.world
    body = PORTRAIT_ASK.format(setting=f"Setting: {world.description[:600]}" if world and world.description else "",
                               name=character.name, about=" ".join((card.get("description") or "").split())[:700])
    person = who(" ".join((card.get(k) or "") for k in ("description", "personality", "first_mes")))
    for _ in range(2):
        line = clean(await _ask(body), {character.name: person})
        if line:
            # The image model is told who it is first: "a person" leaves it to guess.
            return f"portrait of {person}, {line}, {STYLE}"
    raise ImagesUnavailable("Couldn't describe them for a portrait. Try again.")
