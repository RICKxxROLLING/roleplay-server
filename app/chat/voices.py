"""Reading messages aloud, with Piper on the CPU, so it never competes with chat for the GPU.

About 25 times faster than real time on a desktop CPU, with 3 to 4 s to load a voice. A voice
is a 63 MB download from Hugging Face the first time it's used, and nothing is fetched until
the owner presses something: no voice means no network and no memory use. Rendered speech is
kept on disk, since listening to the same line twice is the usual case.

Only voices from `VOICES` are ever fetched: the id picks a row, and the URL and file name
come from the row, never from the request. Piper is imported on first use, so a server can
run without it and just says voices aren't available.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import threading
import urllib.request
import wave
from collections import OrderedDict
from dataclasses import dataclass

from ..config import settings
from ..db.models import Character, ChatSession, Message
from . import group

log = logging.getLogger(__name__)

BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en"
MODES = ("dialogue", "story", "everything")
SAMPLE = "It's been a long day, but the fire's going and there's stew on the stove. Come in and sit down."
#: A long reply is about 2,500 characters; this only stops a runaway.
MAX_CHARS = 6000
PAUSE_SECONDS = 0.25
#: Each loaded voice holds its model in memory.
KEEP_LOADED = 3
KEEP_RENDERED = 400


@dataclass(frozen=True)
class Voice:
    id: str
    label: str
    gender: str
    accent: str


VOICES = {v.id: v for v in (
    Voice("en_US-amy-medium", "Amy", "woman", "American"),
    Voice("en_US-kristin-medium", "Kristin", "woman", "American"),
    Voice("en_US-hfc_female-medium", "Hannah", "woman", "American"),
    Voice("en_US-lessac-medium", "Lessac", "woman", "American"),
    Voice("en_GB-jenny_dioco-medium", "Jenny", "woman", "British"),
    Voice("en_GB-cori-medium", "Cori", "woman", "British"),
    Voice("en_US-ryan-medium", "Ryan", "man", "American"),
    Voice("en_US-joe-medium", "Joe", "man", "American"),
    Voice("en_US-norman-medium", "Norman", "man", "American"),
    Voice("en_US-hfc_male-medium", "Henry", "man", "American"),
    Voice("en_GB-alan-medium", "Alan", "man", "British"),
    Voice("en_GB-northern_english_male-medium", "Northern", "man", "British"),
)}
DEFAULT_NARRATOR = "en_GB-alan-medium"

_SHE = re.compile(r"\b(she|her|hers|herself)\b", re.I)
_HE = re.compile(r"\b(he|him|his|himself)\b", re.I)
_WOMAN = re.compile(r"\b(woman|girl|sister|mother|daughter|wife|aunt|widow)\b", re.I)
_MAN = re.compile(r"\b(man|boy|brother|father|son|husband|uncle|widower)\b", re.I)
_QUOTE = re.compile(r'"([^"]+)"|“([^”]+)”')
_LOCK = threading.Lock()
_loaded: OrderedDict[str, object] = OrderedDict()


class VoiceUnavailable(RuntimeError):
    """Why nothing can be read, in words for the owner."""


def is_installed() -> bool:
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return True


def folder() -> str:
    path = os.path.join(settings.data_dir, "voices")
    os.makedirs(path, exist_ok=True)
    return path


def downloaded(voice_id: str) -> bool:
    base = os.path.join(folder(), f"{voice_id}.onnx")
    return os.path.exists(base) and os.path.exists(base + ".json")


def default_for(char: Character) -> str:
    """A voice to start from: by the pronouns on the card, spread out by character. The
    owner's choice in the editor overrides it."""
    text = (char.card or {}).get("description") or ""
    she, he = len(_SHE.findall(text)), len(_HE.findall(text))
    # A thin card may have no pronouns at all. Live: "Joel's brother" got a woman's voice.
    if not she and not he:
        she, he = len(_WOMAN.findall(text)), len(_MAN.findall(text))
    pool = [v.id for v in VOICES.values() if she == he or v.gender == ("woman" if she > he else "man")]
    return pool[char.id % len(pool)]


def voice_of(char: Character | None) -> str:
    if char is None:
        return settings.voices_narrator
    return char.voice if char.voice in VOICES else default_for(char)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_~#`]", "", text)).strip()


def segments(content: str, mode: str, voice: str, narrator: str, plain: bool = False) -> list[tuple[str, str]]:
    """(voice, text) in reading order. `plain` is a line with no scene to separate: a text
    message or the narrator's own."""
    content = content[:MAX_CHARS]
    if plain or mode == "everything":
        text = _clean(content)
        return [(voice, text)] if text else []
    pieces, at = [], 0
    for m in _QUOTE.finditer(content):
        pieces.append((narrator, content[at:m.start()]))
        pieces.append((voice, m.group(1) or m.group(2)))
        at = m.end()
    pieces.append((narrator, content[at:]))
    if mode == "story":
        out = [(v, _clean(t)) for v, t in pieces]
    elif len(pieces) > 1:
        out = [(voice, _clean(t)) for _, t in pieces[1::2]]
    else:
        # No quotes at all. Bare words are speech; a reply that's all *action* has none.
        out = [(voice, _clean(re.sub(r"\*[^*]*\*", " ", content)))]
    return [(v, t) for v, t in out if re.search(r"\w", t)]


def message_segments(session: ChatSession, message: Message) -> list[tuple[str, str]]:
    narrator = settings.voices_narrator
    if message.role == group.NARRATOR_ROLE:
        return segments(message.content, "everything", narrator, narrator, plain=True)
    speaker = message.speaker if message.speaker_id else session.character
    return segments(message.content, settings.voices_mode, voice_of(speaker), narrator, plain=bool(message.texted))


def _fetch(voice_id: str) -> str:
    """The model's path, downloading it if this is the first use."""
    lang, name, quality = voice_id.split("-")
    base = os.path.join(folder(), f"{voice_id}.onnx")
    for ext in (".json", ""):
        path = base + ext
        if os.path.exists(path):
            continue
        partial = f"{path}.part"
        try:
            urllib.request.urlretrieve(f"{BASE}/{lang}/{name}/{quality}/{voice_id}.onnx{ext}", partial)
            os.replace(partial, path)
        except OSError as exc:
            if os.path.exists(partial):
                os.remove(partial)
            raise VoiceUnavailable(f"Couldn't download the voice: {exc}") from exc
    return base


def _load(voice_id: str):
    if voice_id in _loaded:
        _loaded.move_to_end(voice_id)
        return _loaded[voice_id]
    try:
        from piper import PiperVoice
    except ImportError as exc:
        raise VoiceUnavailable("Voices aren't installed on this server.") from exc
    _loaded[voice_id] = PiperVoice.load(_fetch(voice_id))
    while len(_loaded) > KEEP_LOADED:
        _loaded.popitem(last=False)
    return _loaded[voice_id]


def _synthesise(voice_id: str, text: str, speed: float) -> tuple[int, bytes]:
    """(sample rate, 16-bit mono frames)."""
    from piper import SynthesisConfig

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        _load(voice_id).synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=1 / speed))
    buf.seek(0)
    with wave.open(buf, "rb") as r:
        return r.getframerate(), r.readframes(r.getnframes())


def _trim(cache: str) -> None:
    files = sorted((e for e in os.scandir(cache) if e.name.endswith(".wav")), key=lambda e: e.stat().st_mtime)
    for entry in files[:max(0, len(files) - KEEP_RENDERED)]:
        os.remove(entry.path)


def _render(parts: list[tuple[str, str]], speed: float) -> str:
    key = hashlib.sha256(json.dumps([parts, speed]).encode()).hexdigest()
    cache = os.path.join(folder(), "rendered")
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, f"{key}.wav")
    # One at a time: this is CPU work, and two presses of one line should render it once.
    with _LOCK:
        if os.path.exists(path):
            os.utime(path)
            return path
        rate, frames = 22050, []
        for i, (voice, text) in enumerate(parts):
            rate, audio = _synthesise(voice, text, speed)
            if i:
                frames.append(b"\0\0" * int(rate * PAUSE_SECONDS))
            frames.append(audio)
        partial = f"{path}.part"
        with wave.open(partial, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"".join(frames))
        os.replace(partial, path)
        _trim(cache)
    return path


async def render(parts: list[tuple[str, str]]) -> str:
    """The path of a WAV of these segments. Off the event loop, so chat keeps streaming."""
    speed = min(1.5, max(0.7, settings.voices_speed))
    try:
        return await asyncio.to_thread(_render, parts, speed)
    except VoiceUnavailable:
        raise
    except Exception as exc:
        log.warning("speech failed: %s", exc)
        raise VoiceUnavailable(f"Couldn't read that aloud: {exc}") from exc
