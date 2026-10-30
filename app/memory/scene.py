"""Scene state: where the chat is, when, the weather, and who is present.

Updated after every reply by a short extra generation, and handed back to the
model just before the next one, so it stops losing track of the room it is in.
It is also the base layer later features read from (the narrator, pacing, the
characters' own thoughts).

Prototyped on Stheno against a 14-turn live transcript before building:
- Cost: about 0.6 s per update once the model is loaded (~20-30 tokens out).
- A prompt saying "change a field only when the exchange changes it" made the
  model stick: from turn 8 it kept "inside the stable, late morning" through a
  patrol in the snowy woods and "back home that evening".
- Asking where the exchange *ends*, and naming moves and time skips, followed
  every change in that transcript (porch at night, the stables next morning,
  the treeline, home that evening, outside in the snow).
- Asking for weather with example phrases made it parrot them and drift ("distant
  hum of conversation"); without, weather is mostly an indoors/outdoors flag.
  Shipped without -- location, time and who is present matter more, and every
  field is editable in the chat header.
"""
from __future__ import annotations

import re

from ..cards.models import CharacterCard
from ..config import settings
from ..llm import GenerationParams, get_client
from ..prompt.formats import ALL_MARKERS, get_format

FIELDS = ("location", "time", "weather", "present")

SCENE_PROMPT = """You track the scene of an ongoing roleplay between {user} and {char}.

SCENE BEFORE THIS EXCHANGE:
{scene}

LATEST EXCHANGE:
{user}: {user_text}
{char}: {reply}

Where and when does this exchange END? Read both lines. If anyone moved somewhere, if time \
passed ("later", "the next morning", "that evening"), or if someone arrived or left, the \
scene has changed: update it. Keep a field the same only if nothing in the exchange touches it.

Reply with exactly four short lines:
Location: the place at the end of the exchange
Time: time of day at the end of the exchange
Weather: outdoor conditions if they are outside, or "indoors"
Present: everyone in the scene at the end, comma separated"""

#: Longer than this is description, not a field value.
_MAX_FIELD = 80
_LINE = re.compile(r"^\s*(location|time|weather|present)\s*:\s*(.+?)\s*$", re.I)


def blank(char_name: str, user_name: str) -> dict:
    return {"location": "unknown", "time": "unknown", "weather": "unknown",
            "present": f"{char_name}, {user_name}"}


def render(scene: dict | None) -> str:
    """Four `Field: value` lines, the shape both the prompt and the parser use."""
    if not scene:
        return ""
    return "\n".join(f"{f.capitalize()}: {scene.get(f, 'unknown')}" for f in FIELDS)


def parse_scene(raw: str) -> dict | None:
    """All four fields, each short and quote-free, or None to keep the old scene."""
    text = "Location:" + raw
    for marker in ALL_MARKERS:
        text = text.split(marker, 1)[0]
    found: dict[str, str] = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if m and m.group(1).lower() not in found:
            value = m.group(2).strip().strip(".")
            if not value or len(value) > _MAX_FIELD or '"' in value or "*" in value:
                return None
            found[m.group(1).lower()] = value
    return found if set(found) == set(FIELDS) else None


def build_scene_prompt(scene: dict, user_text: str, reply: str, char: str, user: str) -> str:
    fmt = get_format(settings.prompt_format)
    body = SCENE_PROMPT.format(
        user=user, char=char, scene=render(scene),
        # Long replies are trimmed: where a scene ends is decided by its last
        # lines, and the update has to stay cheap.
        user_text=user_text.strip()[:600], reply=reply.strip()[-1500:],
    )
    # Starting the answer keeps an RP tune in form-filling mode.
    return fmt.user.format(text=body) + fmt.joiner + fmt.response_open + "Location:"


async def update_scene(
    scene: dict | None, user_text: str, reply: str, card: CharacterCard, user_name: str
) -> dict:
    """The scene at the end of this exchange; `scene` unchanged if the model misfires."""
    char = card.name or "Character"
    base = dict(scene) if scene else blank(char, user_name)
    params = GenerationParams(
        temperature=0.2, top_p=0.9, top_k=40, repeat_penalty=1.0,
        context_tokens=settings.context_tokens, max_new_tokens=80,
        stop=list(get_format(settings.prompt_format).stops),
    )
    raw = await get_client().generate(
        build_scene_prompt(base, user_text, reply, char, user_name), params
    )
    return parse_scene(raw) or base
