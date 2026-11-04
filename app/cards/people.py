"""Generated people: a new person for a world, drafted for the owner to keep or discard.

Prototyped on Stheno over 20 drafts. Left to choose, it wrote the same person six times:
young, athletic, scarred, "Blackwood" or "Hawthorne", alone in Seattle. What fixed it:
  - The server picks the name, age, job and temperament, and the model writes the person.
    All four came through. A name from a list also can't clash with an existing card.
  - The tie to an existing character is a pass of its own, after the card. Put in the brief,
    the opening scene greeted that character in 10 of 13 drafts, which makes the user them.
    The cast list stays out of the card prompt for the same reason.
Adults only: the server states the age, and a draft that reads as a minor is refused. A
generated card never gets the `romance` tag, so earned trust keeps it platonic until the
owner tags it (memory/trust.py).
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import AsyncIterator

from ..config import settings
from ..db.models import Character, World
from ..llm import GenerationParams, get_client
from ..memory import trust
from ..prompt.formats import get_format
from . import creator
from .models import CharacterCard

TAG = "generated"

ROLES = [
    "makes or mends things for a living", "looks after animals", "trades or carries goods from place to place",
    "keeps the records and knows everyone's business", "tends the sick and injured", "cooks for other people",
    "stands guard or keeps watch", "arrived here recently and has not said much about why",
    "teaches the young", "grows food",
]
MOODS = [
    "cheerful and talkative", "shy and careful with words", "blunt and impatient", "dreamy and easily distracted",
    "warm and nosy", "sly and funny", "anxious and over-prepared", "calm and hard to read",
]
WOMEN = [
    "Marisol", "Greta", "Priya", "Lucinda", "Noor", "Agnes", "Beatrix", "Colette", "Delphine", "Esther", "Farah",
    "Harriet", "Imogen", "Josephine", "Keiko", "Leona", "Mabel", "Nadia", "Odette", "Paloma", "Rosalind", "Sunita",
    "Tamsin", "Ursula", "Vivian", "Winifred", "Yolanda", "Zofia", "Bronwyn", "Ingrid",
]
MEN = [
    "Hollis", "Tobias", "Anselm", "Desmond", "Otis", "Ambrose", "Barnaby", "Cormac", "Dmitri", "Emeka", "Fergus",
    "Gideon", "Hector", "Ignatius", "Jonah", "Kofi", "Lionel", "Mateo", "Nikolai", "Osman", "Percival", "Quentin",
    "Rafael", "Silas", "Thaddeus", "Ulrich", "Vikram", "Walter", "Yusuf", "Zachary",
]
SURNAMES = [
    "Varga", "Pike", "Okafor", "Lindqvist", "Marsh", "Delacroix", "Tran", "Boone", "Reyes", "Haddad", "Whitlock",
    "Sato", "Abara", "Bellamy", "Castellano", "Dunmore", "Eriksen", "Fenwick", "Gallagher", "Holloway", "Iyer",
    "Jankowski", "Kessler", "Lowell", "Moreau", "Novak", "Osei", "Pruitt", "Quigley", "Rasmussen", "Sandoval",
    "Tolliver", "Underhill", "Vance", "Wexler", "Yates", "Zielinski",
]

BRIEF = ("A new person who lives in this world: {gender} aged {age}, who {role}. By temperament {mood}. "
         "An ordinary person, not a hero or a villain.{hint}")
TIE_PROMPT = """You write character cards for roleplay.

{name}: {about}

{other}: {other_about}

Write one sentence saying how {first} and {other} know each other. They are ordinary \
acquaintances, through work or daily life here. Say only that, and keep to what is written above."""
#: A tie runs to one sentence, and a second try costs about a second.
TIE_ATTEMPTS = 3
TIE_CHARS = 240
_SENTENCE = re.compile(r"[.!?][\"”]?(?=\s|$)")
_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty"}
_ONES = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


@dataclass
class Seed:
    name: str
    gender: str
    age: int
    role: str
    mood: str


def _first(name: str) -> str:
    return name.split()[0] if name.strip() else ""


def pick_seed(taken: set[str], rng: random.Random | None = None) -> Seed:
    """Nobody shares a first name or a whole name with anyone already here: a second
    "Otis" is as confusing in a chat as a second Otis Boone."""
    rng = rng or random.Random()
    used = {_first(n).lower() for n in taken} | {n.lower() for n in taken}
    gender = rng.choice(("a woman", "a man"))
    pool = [n for n in (WOMEN if gender == "a woman" else MEN) if n.lower() not in used]
    # Everyone on the list is used: repeat a first name, and the surname keeps them apart.
    first = rng.choice(pool or (WOMEN if gender == "a woman" else MEN))
    # Three Rasmussens in sixteen drafts read as one family nobody meant to write.
    families = {n.split()[-1].lower() for n in taken if " " in n}
    surnames = [s for s in SURNAMES if s.lower() not in families] or SURNAMES
    names = [f"{first} {s}" for s in surnames]
    free = [n for n in names if n.lower() not in used]
    return Seed(name=rng.choice(free or names), gender=gender, age=rng.randint(22, 66),
                role=rng.choice(ROLES), mood=rng.choice(MOODS))


def brief_for(seed: Seed, hint: str = "") -> str:
    return BRIEF.format(gender=seed.gender, age=seed.age, role=seed.role, mood=seed.mood,
                        hint=f" Also: {hint.strip()}" if hint.strip() else "")


def pick_tie(world: World, rng: random.Random | None = None) -> Character | None:
    """Someone in the world to know. Main characters, not extras, and never a minor:
    a stranger's card has no business hanging its history on a child."""
    people = []
    for c in world.characters:
        if c.extra:
            continue
        if not trust.is_minor(CharacterCard.model_validate(c.card or {"name": c.name})):
            people.append(c)
    return (rng or random).choice(people) if people else None


def judge_tie(raw: str, opening: str, names: list[str]) -> str:
    """One whole sentence about the two of them, or "" to go without."""
    text = f"{opening}{raw}".strip()
    end = _SENTENCE.search(text)
    if not end:
        return ""
    line = text[:end.end()].strip()
    if len(line) > TIE_CHARS or "{{" in line or '"' in line or "*" in line:
        return ""
    if any(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", line, re.I) for n in names if n):
        return ""
    return line


async def write_tie(seed_name: str, description: str, other: Character, user_names: list[str]) -> str:
    fmt = get_format(settings.prompt_format)
    first = _first(seed_name)
    other_about = ((other.card or {}).get("description") or "")[:400]
    body = TIE_PROMPT.format(name=seed_name, first=first, about=description[:600],
                             other=other.name, other_about=other_about)
    opening = f"{first} knows {other.name} from"
    prompt = fmt.user.format(text=body) + fmt.joiner + fmt.response_open + opening
    params = GenerationParams(
        temperature=0.8, top_p=0.95, top_k=50, min_p=0.05, repeat_penalty=1.05,
        context_tokens=2048, max_new_tokens=70, stop=list(fmt.stops),
    )
    client = get_client()
    for _ in range(TIE_ATTEMPTS):
        line = judge_tie(await client.generate(prompt, params), opening, user_names)
        if line:
            return line
    return ""


def _state_age(description: str, seed: Seed) -> str:
    """Every generated card says how old its person is, so nothing downstream has to guess."""
    tens, ones = divmod(seed.age, 10)
    word = _TENS[tens] + (f"[- ]{_ONES[ones]}" if ones else "")
    # 23, twenty-three, "in her sixties", "in his 60s": it said so, in its own words.
    # Seen live without this: "Nadia is 23. Nadia, at twenty-three, is..."
    said = rf"\b{seed.age}\b|\b{word}\b|\b{_TENS[tens][:-1]}ies\b|\b{tens}0s\b"
    if re.search(said, description, re.I):
        return description
    return f"{_first(seed.name)} is {seed.age}. {description}"


async def stream_person(
    world: World, *, taken: set[str], user_names: list[str], hint: str = "", rng: random.Random | None = None,
) -> AsyncIterator[dict]:
    """Token events, then one `draft` event, as cards/creator.py. Nothing is saved here."""
    seed = pick_seed(taken, rng)
    tie = pick_tie(world, rng)
    fields: dict[str, str] = {}
    missing: list[str] = []
    async for event in creator.stream_draft(brief_for(seed, hint), world, name=seed.name):
        if event["type"] != "draft":
            yield event
            continue
        fields, missing = event["fields"], event["missing"]
    fields["name"] = seed.name
    if fields.get("description"):
        fields["description"] = _state_age(fields["description"], seed)
    card = CharacterCard(name=seed.name, description=fields.get("description", ""),
                         personality=fields.get("personality", ""))
    if trust.is_minor(card):
        yield {"type": "refused", "message": f"{seed.name} came out reading as under 18. Draft again."}
        return
    line = ""
    if tie is not None and fields.get("description"):
        line = await write_tie(seed.name, fields["description"], tie, user_names)
        if line:
            fields["description"] = f"{fields['description']}\n\n{line}"
    yield {"type": "draft", "fields": fields, "missing": missing, "tags": [TAG],
           "tie": tie.name if tie is not None and line else None,
           "seed": {"age": seed.age, "role": seed.role, "mood": seed.mood}}
