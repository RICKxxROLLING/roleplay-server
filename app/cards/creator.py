"""Character creator: draft a full V2 card from a short idea, or flesh out a thin one.

Thin cards are the main cause of flat replies -- a two-sentence description
gives an 8B nothing to stay in character with. The draft is never saved here:
it goes back to the editor, and the user keeps, edits or throws it away.

Sections with labels, not JSON. An RP tune asked for JSON writes prose with
braces around it; a labelled form, prefilled with "Name:", it fills in the way
it fills the scene tracker (memory/scene.py), and a section that comes back
malformed costs that section, not the whole draft.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import AsyncIterator

from ..config import settings
from ..llm import GenerationParams, get_client
from ..prompt.formats import ALL_MARKERS, get_format
from .models import CharacterCard

#: Label in the model's output -> card field. Order is the order asked for.
SECTIONS = {
    "Name": "name",
    "Description": "description",
    "Personality": "personality",
    "Scenario": "scenario",
    "First message": "first_mes",
    "Example dialogue": "mes_example",
}

CREATE_PROMPT = """You write character cards for roleplay. Write a card for this idea:

{brief}
{setting}{existing}
Write every section below, in this order, each starting with its label on a new line:

Name: the character's name and nothing else.
Description: two or three paragraphs in third person -- appearance, history, what they want, \
and their situation now. Concrete details: places, people, possessions, habits.
Personality: one paragraph -- traits, how they talk, what they hide.
Scenario: one or two sentences -- where the story opens and how {{{{char}}}} knows {{{{user}}}}.
First message: the opening scene, written as {{{{char}}}}'s narrator in third person, with \
actions in *asterisks* and speech in quotes. Set the scene with sensory detail, then end on \
something {{{{char}}}} says or does that {{{{user}}}} can answer.
Example dialogue: two short exchanges, each starting with <START>, every line labelled \
{{{{user}}}}: or {{{{char}}}}:, showing how {{{{char}}}} talks.

Write {{{{char}}}} for the character and {{{{user}}}} for the person they talk to, never a name \
for {{{{user}}}}.{style}"""

EXPAND_EXISTING = """
The card already exists. Keep every name, fact and detail in it, and build on it -- \
add, don't replace. Write every section, including the ones it doesn't have yet:

{card}
"""

_LABEL = re.compile(
    r"^[ \t*#]*(" + "|".join(re.escape(k) for k in SECTIONS) + r")[ \t*]*:[ \t*]*", re.M | re.I
)


@dataclass
class Draft:
    fields: dict[str, str] = field(default_factory=dict)
    #: Sections the model left out or that failed their check, for the UI.
    missing: list[str] = field(default_factory=list)
    raw: str = ""


def _clean(text: str) -> str:
    for marker in ALL_MARKERS:
        text = text.replace(marker, "")
    return text.strip().strip("-").strip()


def parse_draft(raw: str) -> Draft:
    """Split the labelled sections; each is judged on its own."""
    text = "Name:" + raw if not _LABEL.match(raw.lstrip()) else raw
    draft = Draft(raw=raw)
    marks = list(_LABEL.finditer(text))
    for i, m in enumerate(marks):
        key = next(k for k in SECTIONS if k.lower() == m.group(1).lower())
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        value = _clean(text[m.end():end])
        if SECTIONS[key] not in draft.fields and value:
            draft.fields[SECTIONS[key]] = value

    name = draft.fields.get("name", "")
    # A name is one short line; anything else is the model running on.
    name = name.splitlines()[0].strip(' "*') if name else ""
    if not name or len(name) > 60:
        draft.fields.pop("name", None)
    else:
        draft.fields["name"] = name
    # The example dialogue sometimes follows the opening scene unlabelled,
    # starting at its first <START>. Seen on Stheno.
    first = draft.fields.get("first_mes", "")
    if "<START>" in first:
        head, _, tail = first.partition("<START>")
        draft.fields["first_mes"] = head.strip()
        draft.fields.setdefault("mes_example", "<START>" + tail.rstrip())
        first = head.strip()
    # "As the door chimes, Laura rises.* "Ah..." -- a narration span whose
    # opening asterisk the model dropped, which breaks the chat's styling.
    if first.count("*") % 2 and not first.startswith("*"):
        draft.fields["first_mes"] = "*" + first
    ex = draft.fields.get("mes_example", "").replace("<END>", "").strip()
    if ex:
        draft.fields["mes_example"] = ex
    if ex and "<START>" not in ex:
        draft.fields["mes_example"] = "<START>\n" + ex
    for f, minimum in (("description", 200), ("first_mes", 80)):
        if len(draft.fields.get(f, "")) < minimum:
            draft.fields.pop(f, None)
    draft.missing = [k for k, f in SECTIONS.items() if f not in draft.fields]
    return draft


def _setting(world) -> str:
    if world is None:
        return ""
    people = ", ".join(c.name for c in world.characters)
    lore = ", ".join(e.get("name") or ", ".join(e.get("keys") or []) for e in (world.entries or []))
    parts = [f"\nIt is set in {world.name}: {(world.description or '').strip()}"]
    if lore:
        parts.append(f"Known in this world: {lore}.")
    if people:
        # Seen on Stheno: with the cast listed and nothing more, the opening
        # scene greeted Tommy -- the user had become one of them.
        parts.append(f"Other people there, who the character may know: {people}. "
                     "{{user}} is none of them; they are someone new to this card.")
    return "\n".join(parts) + "\n"


def _existing(card: CharacterCard | None) -> str:
    if card is None:
        return ""
    shown = [f"{k}: {getattr(card, f)}" for k, f in SECTIONS.items() if getattr(card, f, "").strip()]
    return EXPAND_EXISTING.format(card="\n".join(shown))


def build_draft_prompt(brief: str, world=None, existing: CharacterCard | None = None) -> str:
    fmt = get_format(settings.prompt_format)
    style = settings.narration_style.strip()
    body = CREATE_PROMPT.format(
        brief=brief.strip() or (existing.name if existing else ""),
        setting=_setting(world),
        existing=_existing(existing),
        style=f"\n\nThe server's narration style, which the first message should follow:\n{style}"
        if style else "",
    )
    prefill = "Name:" if existing is None else f"Name: {existing.name}\nDescription:"
    return fmt.user.format(text=body) + fmt.joiner + fmt.response_open + prefill


async def stream_draft(
    brief: str, world=None, existing: CharacterCard | None = None
) -> AsyncIterator[dict]:
    """Token events, then one `draft` event with the parsed fields."""
    params = GenerationParams(
        temperature=0.9, top_p=0.95, top_k=50, min_p=0.05, repeat_penalty=1.05,
        repeat_last_n=1024, context_tokens=settings.context_tokens, max_new_tokens=1400,
        stop=list(get_format(settings.prompt_format).stops),
    )
    prompt = build_draft_prompt(brief, world, existing)
    prefix = "" if existing is None else f"{existing.name}\nDescription:"
    pieces: list[str] = []
    async for piece in get_client().generate_stream(prompt, params):
        pieces.append(piece)
        yield {"type": "token", "text": piece}
    draft = parse_draft(prefix + "".join(pieces))
    yield {"type": "draft", "fields": draft.fields, "missing": draft.missing}
