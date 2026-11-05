"""Speaking instead of typing: the owner's recording turned into text, on this server.

faster-whisper on the CPU, rather than the browser's own speech recognition, which on most
browsers sends the audio to the vendor's servers. A roleplay chat has no business there.
The recording is deleted as soon as it's read, and the words go to the type box for the owner
to check and send: a mishearing shouldn't reach the model unseen.

Measured over 16 clips of Piper reading roleplay lines, on a desktop CPU. Clean speech, so a
real voice in a real room will do worse:
    tiny.en   10x real time   14/16 exact (16/16 told the names)
    base.en    5x real time   16/16
    small.en   2x real time   15/16
base.en it is, with the names in this chat as a hint, since names are what it can't guess.
The model is about 140 MB, downloaded the first time someone speaks.

The voice activity filter is on because Whisper invents words for silence: a press of the
mic with nothing said should come back empty, not as "Thank you."
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading

from ..config import settings
from ..db.models import ChatSession
from . import group

log = logging.getLogger(__name__)

MODEL = "base.en"
MAX_BYTES = 25 * 1024 * 1024
#: The model keeps about 224 tokens of hint and drops the start if it's longer.
MAX_HINT = 500
_LOCK = threading.Lock()
_model = None


class ListenUnavailable(RuntimeError):
    """Why nothing could be heard, in words for the owner."""


def hint_for(session: ChatSession) -> str:
    """Names this chat is likely to mention, most likely first."""
    names = [session.persona.name] if session.persona else []
    names += [c.name for c in group.cast(session)]
    world = group.world_of(session)
    if world:
        names += [c.name for c in world.characters]
        names += [e.get("name") or "" for e in (world.entries or [])]
    seen, out = set(), []
    for name in names:
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return ", ".join(out)[:MAX_HINT]


def _load():
    global _model
    if _model is None:
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ListenUnavailable("Speech to text isn't installed on this server.") from exc
        folder = os.path.join(settings.data_dir, "whisper")
        os.makedirs(folder, exist_ok=True)
        _model = WhisperModel(MODEL, device="cpu", compute_type="int8", download_root=folder)
    return _model


def _transcribe(path: str, hint: str) -> str:
    with _LOCK:
        segments, _ = _load().transcribe(path, language="en", initial_prompt=hint or None, beam_size=1, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


async def hear(path: str, hint: str = "") -> str:
    """The words in a recording, "" if nothing was said. Off the event loop."""
    try:
        return await asyncio.to_thread(_transcribe, path, hint)
    except ListenUnavailable:
        raise
    except Exception as exc:
        log.warning("speech to text failed: %s", exc)
        raise ListenUnavailable("Couldn't make that recording out. Try again.") from exc
