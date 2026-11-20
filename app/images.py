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
from typing import NamedTuple

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
_known_files: dict[str, list[str]] = {}


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
    global _known, _known_files
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
            files = {key: await _choices(http, base, node_name, field)
                     for key, node_name, field in _FILE_LISTS}
    except Exception as exc:  # anything at all: the PC is off, asleep, or not ComfyUI
        if await _waker_says():
            # Stopped on purpose: it starts again when a picture is asked for.
            return {"ok": False, "configured": True, "private": False, "asleep": True, "checkpoints": _known, **_known_files,
                    "error": "ComfyUI is stopped on your PC to keep its graphics card free. It starts by itself "
                             "when you ask for a picture, which makes that first one slower."}
        return {"ok": False, "configured": True, "private": False, "checkpoints": [],
                "error": f"ComfyUI didn't answer at {base}. Is the PC on and ComfyUI running? ({type(exc).__name__})"}
    _known, _known_files = list(names), files
    return {"ok": private, "configured": True, "private": private, "checkpoints": list(names), **files,
            "error": None if private else NOT_PRIVATE}


#: Files Settings can offer, read from the ComfyUI nodes that load them.
_FILE_LISTS = (("loras", "LoraLoaderModelOnly", "lora_name"), ("unets", "UNETLoader", "unet_name"),
               ("text_encoders", "DualCLIPLoader", "clip_name1"), ("vaes", "VAELoader", "vae_name"))


async def _choices(http, base: str, node: str, field: str) -> list[str]:
    try:
        r = await http.get(f"{base}/object_info/{node}")
        return list(r.json()[node]["input"]["required"][field][0]) if r.status_code == 200 else []
    except Exception:
        return []


#: The detail pass: the first picture scaled up 1.5x and sampled again lightly. At 1216x832 a
#: face is a small part of the frame, and SDXL draws small eyes badly (uneven, smeared). Same
#: scene, same seeds, Juggernaut XL: eyes came out even and sharp with it, and the picture kept
#: its layout at this strength. 14 s became 35 s on the owner's RX 9060 XT.
DETAIL_SCALE = 1.5
DETAIL_DENOISE = 0.4
DETAIL_STEPS = 20


#: Each person drawn in their own strip of the frame, overlapping a little so they can stand
#: close. One prompt naming two people gave one person with both their details: same scene,
#: same three seeds, two people in 0 of 3 (Sadie wore Riley's glasses each time), and in 3 of 3
#: with each in their own area, everyone in their own clothes. ComfyUI's own nodes, no add-ons.
AREA_OVERLAP = 0.1
#: Live, a long description of the place gave a wide shot of the whole room, and at that size
#: the faces lost who they were (Sadie came out blonde). People are the point of these pictures.
SHOT = "medium shot"
_COUNT = {2: "two", 3: "three", 4: "four"}


def _people_conditioning(g: dict, people: list[str], clip: list) -> list:
    """Each person's text in their own strip, combined with the whole-frame text in node 2."""
    pos: list = ["2", 0]
    n = len(people)
    for i, text in enumerate(people):
        x = max(0.0, i / n - AREA_OVERLAP / 2)
        width = min(1.0 - x, 1 / n + AREA_OVERLAP)
        g[f"p{i}"] = {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": clip}}
        g[f"a{i}"] = {"class_type": "ConditioningSetAreaPercentage", "inputs": {
            "conditioning": [f"p{i}", 0], "width": round(width, 3), "height": 1.0, "x": round(x, 3), "y": 0.0,
            "strength": 1.0}}
        g[f"c{i}"] = {"class_type": "ConditioningCombine", "inputs": {"conditioning_1": pos,
                                                                     "conditioning_2": [f"a{i}", 0]}}
        pos = [f"c{i}", 0]
    return pos


#: What a LoRA is for: every picture, scenes, selfies (texting mode) or portraits.
LORA_USES = ("always", "scenes", "selfies", "portraits")


def loras_for(kind: str) -> list[dict]:
    """The LoRAs from Settings that apply to this kind of picture, in order."""
    out = []
    for item in settings.image_loras or []:
        if isinstance(item, dict) and item.get("file") and item.get("use", "always") in ("always", kind):
            out.append(item)
    return out


def with_triggers(positive: str, loras: list[dict]) -> str:
    """A LoRA's trigger words go first, where its card says they work best."""
    words = [" ".join(str(item.get("trigger") or "").split()) for item in loras]
    words = [w for w in words if w and w.lower() not in positive.lower()]
    return ", ".join([*words, positive])


def _loaders(g: dict, checkpoint: str, loras: list[dict]) -> tuple[list, list, list]:
    """Model, CLIP and VAE outputs, with the LoRAs chained onto the model. Flux can come as one
    checkpoint file or as separate files (`flux_unet` set): diffusion model, two text encoders, VAE."""
    if settings.image_family == "flux" and (settings.flux_unet or "").strip():
        g["1"] = {"class_type": "UNETLoader", "inputs": {"unet_name": settings.flux_unet, "weight_dtype": "default"}}
        g["1c"] = {"class_type": "DualCLIPLoader", "inputs": {
            "clip_name1": settings.flux_clip_l, "clip_name2": settings.flux_t5, "type": "flux"}}
        g["1v"] = {"class_type": "VAELoader", "inputs": {"vae_name": settings.flux_vae}}
        model, clip, vae = ["1", 0], ["1c", 0], ["1v", 0]
    else:
        g["1"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}}
        model, clip, vae = ["1", 0], ["1", 1], ["1", 2]
    for i, item in enumerate(loras):
        g[f"l{i}"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": model, "lora_name": item["file"], "strength_model": float(item.get("strength", 1.0))}}
        model = [f"l{i}", 0]
    return model, clip, vae


def graph(positive: str, negative: str, size: tuple[int, int], checkpoint: str, seed: int,
          people: list[str] | None = None, loras: list[dict] | None = None) -> dict:
    """ComfyUI's API-format graph for a txt2img, ending in the node that saves nothing.

    SDXL: with `people`, `positive` is the whole frame and each person's text gets their own part
    of it; the detail pass when it's on. Flux: one natural-language prompt (its T5 encoder reads
    "on the left ... on the right" well), guidance instead of CFG, and no negative prompt, which
    Flux ignores at CFG 1, so the server's own refusals (`_EXPLICIT`) are what keep it clean."""
    w, h = size
    g: dict = {}
    model, clip, vae = _loaders(g, checkpoint, loras or [])
    flux = settings.image_family == "flux"
    g["2"] = {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": clip}}
    g["3"] = {"class_type": "CLIPTextEncode", "inputs": {"text": "" if flux else negative, "clip": clip}}
    pos: list = ["2", 0]
    if flux:
        g["2g"] = {"class_type": "FluxGuidance", "inputs": {"conditioning": pos, "guidance": settings.flux_guidance}}
        pos = ["2g", 0]
        g["4"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}}
        sampler = {"steps": settings.flux_steps, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple"}
    else:
        if people and len(people) > 1:
            pos = _people_conditioning(g, people, clip)
        g["4"] = {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}}
        sampler = {"steps": settings.image_steps, "cfg": settings.image_cfg, "sampler_name": "dpmpp_2m",
                   "scheduler": "karras"}
    g["5"] = {"class_type": "KSampler", "inputs": {
        "model": model, "positive": pos, "negative": ["3", 0], "latent_image": ["4", 0], "seed": seed,
        "denoise": 1.0, **sampler}}
    g["6"] = {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": vae}}
    g[_SAVE_ID] = {"class_type": PRIVATE_NODE, "inputs": {"images": ["6", 0]}}
    # Flux draws faces cleanly at this size, and a second pass would double a slow picture.
    if settings.image_detail and not flux:
        g["8"] = {"class_type": "ImageScaleBy", "inputs": {"image": ["6", 0], "upscale_method": "lanczos",
                                                            "scale_by": DETAIL_SCALE}}
        g["9"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["8", 0], "vae": vae}}
        g["10"] = {"class_type": "KSampler", "inputs": {
            "model": model, "positive": pos, "negative": ["3", 0], "latent_image": ["9", 0],
            "seed": seed, "steps": DETAIL_STEPS, "cfg": settings.image_cfg,
            "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": DETAIL_DENOISE}}
        g["11"] = {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": vae}}
        g[_SAVE_ID]["inputs"]["images"] = ["11", 0]
    return g


#: Refused in any picture prompt, whichever model draws it. Flux has no negative prompt to lean on.
_EXPLICIT = re.compile(
    r"\b(?:nude|nudity|naked|topless|bottomless|nipples?|genitals?|sex|sexual|sexy|erotic\w*|porn\w*|"
    r"lingerie|underwear|undress\w*|strip\w*|bare (?:breasts?|chest|skin)|breasts?|cleavage|"
    r"wearing nothing|no clothes|without clothes|in (?:her|his|their) underwear|seductive\w*|sensual\w*|"
    r"provocative\w*|lewd|nsfw)\b", re.I)


def explicit(prompt: str) -> bool:
    return bool(_EXPLICIT.search(prompt or ""))


async def render(positive: str, size: tuple[int, int], people: list[str] | None = None,
                 kind: str = "scenes") -> bytes:
    """One PNG from ComfyUI, or ImagesUnavailable with the reason.

    The picture comes back over the live connection and is never written on the PC; then the
    job is deleted from ComfyUI's history, the in-memory list of past prompts its /history
    serves, so the prompt text doesn't stay there either until ComfyUI restarts."""
    if explicit(positive) or any(explicit(p) for p in people or []):
        raise ImagesUnavailable("That picture would be explicit, so it isn't made.")
    base = _base()
    await wake()
    found = await status()
    if not found["private"]:
        raise ImagesUnavailable(found["error"] or NOT_PRIVATE)
    checkpoint = (settings.comfyui_checkpoint or "").strip()
    if not checkpoint and not (settings.image_family == "flux" and (settings.flux_unet or "").strip()):
        if not found["checkpoints"]:
            raise ImagesUnavailable("ComfyUI has no checkpoints installed. Put a model in its models/checkpoints folder.")
        checkpoint = found["checkpoints"][0]
    loras = loras_for(kind)
    client_id = uuid.uuid4().hex
    body = {"prompt": graph(with_triggers(positive, loras), settings.image_negative, size, checkpoint,
                            random.randrange(2**32), people, loras),
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
{earlier}
What has just happened:
{latest}

Describe the moment that has just happened for a painting, in exactly this form, one line each:
Place: the place and what is around, in a few short phrases
Light: the light and the time of day
{forms}
Plain comma-separated phrases. No names after the colons, no dialogue."""
# Asked for one line of phrases about each person's whole look, the model described them afresh
# every time: Sadie came out with long straight hair and glasses, then short curls. So how
# someone looks is written once (`look_of`) and the same words go into every picture; the model
# only says where they are and what they're doing.
LOOK_ASK = """{setting}

About {name}: {about}
{age}
Describe how {name} looks, for a painter who must paint {name} the same way in every picture. \
One line of 8 to 12 short comma-separated phrases, only things a painter can see: {opening}age, \
build, skin, face, hair colour and style, eyes, one outfit they usually wear, and anything that \
marks them out. Whatever is written above about how {name} looks goes in, in its own words; add \
only what fits it. No names."""
# Prototype, Sadie's card says 24: the look said "late 20s", added "quirky, expressive,
# vibrant", and gave two outfits ("overalls at the café, scarves at market"). So the card's
# age is stated and kept, and the asking is for one outfit and only what can be seen.
_AGE = re.compile(r"\b(\d{2})[- ]years?[- ]old\b|,\s*(\d{2}),", re.I)
PORTRAIT_ASK = """{setting}

About {name}: {about}

For a portrait of {name}, write one line of short comma-separated phrases: their expression, how \
they stand or sit, and a simple background that suits them. No names, nothing about their looks."""

#: Words for a scene's style, kept out of what the model writes so it can't drift.
STYLE = "detailed digital painting, cinematic lighting, high quality"
MAX_LOOK_CHARS = 320
#: The newest exchange is given whole: cut at 500 characters, the picture lost what the reply
#: was about. Older lines only set the scene.
LATEST_CHARS = 3000
EARLIER_CHARS = 300


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


def _unname(text: str, stand_in: dict[str, str]) -> str:
    parts = {p: stand_in[full] for full in stand_in for p in (full, full.split()[0]) if p}
    for n in sorted(parts, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(n)}(?!\w)", parts[n], text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip(' ,."*')


def clean(raw: str, names: list[str] | dict[str, str], least: int = 3, cap: int = MAX_PROMPT_CHARS) -> str:
    """One line of phrases, names taken out (an image model doesn't know them), or "". `names`
    may map each name to what stands in for it ("a man"); a plain list uses "a person"."""
    stand_in = names if isinstance(names, dict) else {n: "a person" for n in names}
    line = _unname(raw.strip().split("\n")[0], stand_in)[:cap].rstrip(", ")
    return line if len(line.split(",")) >= least else ""


async def _ask(body: str, opener: str = "") -> str:
    fmt = get_format(settings.prompt_format)
    prompt = fmt.user.format(text=body) + fmt.joiner + fmt.response_open + opener
    params = GenerationParams(temperature=0.6, top_p=0.9, top_k=40, repeat_penalty=1.05,
                              context_tokens=settings.context_tokens, max_new_tokens=220, stop=list(fmt.stops))
    return opener + await get_client().generate(prompt, params)


def not_pictured(characters) -> str:
    """Why nobody here can be pictured, or "". Anyone written as under 18 never is; nor is a card
    with no age when Settings → Safety keeps those platonic."""
    cards = [(c.name, CharacterCard.model_validate(c.card or {"name": c.name})) for c in characters]
    young = [n for n, card in cards if trust.is_minor(card)]
    if young:
        return f"No pictures of {', '.join(young)}: written as under 18."
    unknown = [n for n, card in cards if trust.kept_platonic(card)]
    if unknown:
        return (f"No pictures of {', '.join(unknown)}: their card gives no age, and Settings → Safety "
                "keeps people with no stated age out of pictures.")
    return ""


def _setting(world) -> str:
    return f"Setting: {world.description[:600]}" if world and world.description else ""


def _card_text(character) -> str:
    card = character.card or {}
    return " ".join(" ".join((card.get(k) or "").split()) for k in ("description", "personality"))


def person_of(character, said: str = "") -> str:
    """"a man" or "a woman" for a character: the card and greeting, else their own lines."""
    found = who(_card_text(character) + " " + ((character.card or {}).get("first_mes") or ""))
    return found if found != "a person" else who(said)


async def look_of(target, name: str, about: str, person: str, world) -> str:
    """How `target` (a Character or Persona) looks in pictures: its saved words, or new ones written
    and saved now. The caller commits."""
    if (getattr(target, "look", "") or "").strip():
        return target.look.strip()
    m = _AGE.search(about)
    age = next((g for g in (m.groups() if m else ()) if g), "")
    body = LOOK_ASK.format(setting=_setting(world), name=name, about=about[:900],
                           age=f"{name} is {age} years old.\n" if age else "",
                           opening="" if person != "a person" else "whether they are a man or a woman, then ")
    for _ in range(2):
        line = clean(await _ask(body), {name: person}, least=5, cap=MAX_LOOK_CHARS)
        if line:
            first = line.split(",")[0].lower()
            if person != "a person" and not re.search(r"\b(?:man|woman)\b", first):
                line = f"{person}, {line}"
            if age and age not in line:
                line = re.sub(r"^((?:an? )?(?:man|woman|person)),", rf"\1, {age} years old,", line, flags=re.I) \
                    if re.match(r"(?:an? )?(?:man|woman|person),", line, re.I) else f"{age} years old, {line}"
            target.look = line
            return line
    return person  # Nothing usable: the picture still says who, and the next one tries again.


_FORM_LINE = re.compile(r"^\s*([^:\n]{1,60}):\s*(.+?)\s*$")


def _parse(raw: str, keys: list[str]) -> dict[str, str]:
    got: dict[str, str] = {}
    lowered = {k.lower(): k for k in keys}
    for line in raw.splitlines():
        m = _FORM_LINE.match(line.strip("*- "))
        if m and m.group(1).strip().lower() in lowered:
            got.setdefault(lowered[m.group(1).strip().lower()], m.group(2))
    return got


class Scene(NamedTuple):
    """`text` is the whole prompt as one line, kept with the picture; `frame` and `people` are
    what an SDXL model is sent when there are two or more, each person in their own area; `flux`
    is the same moment in sentences, each person placed in words, for a Flux model."""
    text: str
    frame: str
    people: list[str]
    flux: str = ""

    def for_model(self) -> tuple[str, list[str]]:
        if settings.image_family == "flux":
            return self.flux or self.text, []
        return self.frame, self.people


_PLACES = {2: ("On the left", "On the right"), 3: ("On the left", "In the middle", "On the right")}


def _flux_text(frame: str, each: list[str]) -> str:
    places = _PLACES.get(len(each))
    people = [f"{places[i]}, {e}." if places else f"{e}." for i, e in enumerate(each)]
    lead = f"A medium shot of {_COUNT.get(len(each), len(each))} people" if len(each) > 1 else "A medium shot"
    return " ".join([f"{lead}, {frame}.", *people, f"{STYLE[0].upper()}{STYLE[1:]}."])


async def scene_prompt(session, cast, user: str, recent: list[tuple[str, str]]) -> Scene:
    """Place and light from the model, then each person as their saved look and what the model
    says they're doing. Looks are saved on the characters and persona; the caller commits."""
    from .chat import group

    world = group.world_of(session)
    scene = session.scene or {}
    where = ", ".join(str(scene[k]) for k in ("location", "time", "weather") if scene.get(k) and scene[k] != "unknown")
    meeting = group.current_encounter(session) if session.world_id is not None else None
    if meeting is not None and meeting.sky:
        # The tracker's weather is mostly "indoors"; without the day's sky a drizzly midday
        # was drawn as "late afternoon sun", live.
        where += f", the weather outside {meeting.sky}"
    people: list[tuple[str, str, str]] = []  # name, "a man", look
    for c in cast:
        person = person_of(c, " ".join(text for name, text in recent if name == c.name))
        people.append((c.name, person, await look_of(c, c.name, _card_text(c), person, world)))
    persona = session.persona
    if persona is not None and persona.description:
        about = " ".join(persona.description.replace("{{user}}", user).split())
        person = who(about)
        people.append((user, person, await look_of(persona, user, about, person, world)))
    stand_in = {name: person for name, person, _ in people}
    latest, earlier = recent[-2:], recent[:-2]
    body = SCENE_ASK.format(
        setting=_setting(world), where=where or "unknown",
        people="\n".join(f"- {name}, {person}: {look}" for name, person, look in people),
        earlier=("\nEarlier:\n" + "\n".join(f"{n}: {t[:EARLIER_CHARS]}" for n, t in earlier) + "\n") if earlier else "",
        latest="\n".join(f"{n}: {t[:LATEST_CHARS]}" for n, t in latest),
        forms="\n".join(f"{name}: where {name} is and what {name} is doing, with their expression"
                        for name, _, _ in people))
    keys = ["Place", "Light", *[name for name, _, _ in people]]
    for _ in range(2):
        got = _parse(await _ask(body, "Place:"), keys)
        place = _unname(got.get("Place", ""), stand_in)
        doing = {name: _unname(got[name], stand_in) for name, _, _ in people if got.get(name)}
        if place and doing:
            # Only people the model put in the moment; each as the same words every time.
            frame = ", ".join(p for p in (place, _unname(got.get("Light", ""), stand_in)) if p)
            each = [f"{look}, {doing[name]}" for name, _, look in people if name in doing]
            text = ", ".join([frame, *each]) + f", {STYLE}"
            if len(each) < 2:
                return Scene(f"{SHOT}, {text}", f"{SHOT}, {text}", [], _flux_text(frame, each))
            who_ = " and ".join(person for name, person, _ in people if name in doing)
            count = _COUNT.get(len(each), str(len(each)))
            return Scene(f"{SHOT}, {text}", f"{SHOT}, {count} people in the foreground, {who_}, {frame}, {STYLE}",
                         [f"{e}, {STYLE}" for e in each], _flux_text(frame, each))
    raise ImagesUnavailable("Couldn't describe this moment for a picture. Try again.")


SELFIE_SIZE = (832, 1216)
SELFIE_ASK = """{setting}

{name}, {person}: {look}

Their text conversation so far:
{transcript}

{name} is about to take a selfie on their phone and send it to {user}. Describe it in exactly this form, one line each:
Place: where {name} is right now and what is behind them, in a few short phrases
Light: the light and the time of day
{name}: their pose, expression and what they are doing, in a few short phrases
Plain comma-separated phrases. No names after the colons, nothing else."""
#: Asking for a picture in a text: "send me a pic", "selfie?", "show me where you are".
_ASKS_FOR_PICTURE = re.compile(
    r"\b(?:selfie|send (?:me )?(?:a |another )?(?:pic|picture|photo|pict?)s?|(?:pic|picture|photo) of you|"
    r"show me (?:where you are|what you(?:'re| are) (?:doing|up to|looking at)|you)|let me see you|"
    r"what do you look like)\b", re.I)


def asks_for_picture(text: str) -> bool:
    return bool(_ASKS_FOR_PICTURE.search(text or ""))


async def selfie_prompt(session, character, user: str, recent: list[tuple[str, str]]) -> Scene:
    """A phone selfie from `character` in a text thread: their saved look, and where they are and
    what they're doing from the texts. Looks are saved on the character; the caller commits."""
    from .chat import group

    world = group.world_of(session)
    said = " ".join(text for name, text in recent if name == character.name)
    person = person_of(character, said)
    look = await look_of(character, character.name, _card_text(character), person, world)
    body = SELFIE_ASK.format(setting=_setting(world), name=character.name, person=person, look=look, user=user,
                             transcript="\n".join(f"{n}: {t[:EARLIER_CHARS]}" for n, t in recent))
    stand_in = {character.name: person, user: "someone"}
    for _ in range(2):
        got = _parse(await _ask(body, "Place:"), ["Place", "Light", character.name])
        place, doing = _unname(got.get("Place", ""), stand_in), _unname(got.get(character.name, ""), stand_in)
        if place and doing:
            light = _unname(got.get("Light", ""), stand_in)
            text = ", ".join(p for p in ("smartphone selfie photo, front camera, held at arm's length",
                                         look, doing, place, light) if p)
            flux = (f"A smartphone selfie taken at arm's length with the phone's front camera. {look}, {doing}. "
                    f"Behind them, {place}." + (f" {light[0].upper()}{light[1:]}." if light else ""))
            return Scene(text, text, [], flux)
    raise ImagesUnavailable("Couldn't picture that selfie. Try again.")


async def portrait_prompt(character) -> str:
    """Their saved look, so the avatar is the person in the pictures, with a pose and background."""
    world = character.world
    person = person_of(character)
    look = await look_of(character, character.name, _card_text(character), person, world)
    body = PORTRAIT_ASK.format(setting=_setting(world), name=character.name, about=_card_text(character)[:700])
    for _ in range(2):
        line = clean(await _ask(body), {character.name: person})
        if line:
            return f"portrait of {look}, {line}, {STYLE}"
    raise ImagesUnavailable("Couldn't describe them for a portrait. Try again.")
