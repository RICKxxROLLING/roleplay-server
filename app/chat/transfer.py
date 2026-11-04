"""Move one chat between installs, or keep a copy: its messages, swipes, memory, facts,
scene, journal and standings, plus the people and world it needs.

Importing never changes what's already there. It always makes a new chat, and a character,
persona or world with the same name is reused as it stands, so the file's copy only matters
on an install that doesn't have them. Writing first comes back off, so restoring an old
backup can't start messaging anyone.

Vectors aren't in the file: they belong to an embedding model and index_session rebuilds
them after the next turn. Everything points by position in the file rather than by id, since
ids mean nothing on another install, and the summary's watermark becomes "the first n
messages" because deleted messages leave gaps in the ids.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import os
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session as DbSession

from ..cards.models import CharacterCard, LoreEntry
from ..config import settings
from ..db.models import (Character, CharacterThought, ChatSession, Message, Persona, SessionMember,
                         Standing, World)
from . import group
from .rhythm import REACH

_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
FORMAT = "roleplay-chat"
VERSION = 1
PNG = b"\x89PNG\r\n\x1a\n"
MAX_AVATAR = 16 * 1024 * 1024
#: speaker_id for a line whose character has since been deleted. It reads as "Someone", as
#: it did before the export; None would hand their lines to the chat's own character.
GONE = -1


class WorldOut(BaseModel):
    name: str
    description: str = ""
    entries: list[LoreEntry] = []


class PersonOut(BaseModel):
    name: str
    card: dict[str, Any]
    world: str | None = None
    extra: bool = False
    rhythm: dict[str, Any] | None = None
    voice: str | None = Field(None, max_length=80)
    #: In the scene now, rather than someone who spoke once and left.
    member: bool = False
    avatar: str | None = None


class PersonaOut(BaseModel):
    name: str
    description: str = ""


class LineOut(BaseModel):
    role: Literal["user", "assistant", "narrator"]
    content: str
    created_at: dt.datetime | None = None
    alternatives: list[str] | None = None
    alt_index: int = 0
    pinned: bool = False
    speaker: str | None = None
    speaker_gone: bool = False
    initiated: bool = False
    texted: bool = False


class ThoughtOut(BaseModel):
    character: str
    #: Position in `messages`, if it still has the reply that prompted it.
    message: int | None = None
    journal: str
    view: str
    created_at: dt.datetime | None = None


class StandingOut(BaseModel):
    character: str
    trust: float = 30.0
    affection: float = 30.0
    respect: float = 30.0
    last_reason: str = ""


class ChatFile(BaseModel):
    format: str
    version: int = VERSION
    exported_at: dt.datetime | None = None
    title: str = "Imported chat"
    created_at: dt.datetime | None = None
    character: str
    mode: Literal["story", "texting"] = "story"
    narrator: bool = False
    summary: str = ""
    facts: str = ""
    scene: dict[str, Any] | None = None
    scene_prev: dict[str, Any] | None = None
    #: How many of the first messages the summary already covers.
    folded: int = Field(0, ge=0)
    persona: PersonaOut | None = None
    characters: list[PersonOut] = []
    worlds: list[WorldOut] = []
    messages: list[LineOut] = []
    thoughts: list[ThoughtOut] = []
    standings: list[StandingOut] = []


class ImportRefused(ValueError):
    """The file can't be imported, in words for the owner."""


def _avatar(path: str | None) -> str | None:
    if not path or not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def export_chat(db: DbSession, s: ChatSession) -> dict:
    """The whole chat as JSON-ready data."""
    members = {m.character_id for m in s.members}
    people: dict[int, Character] = {c.id: c for c in group.cast(s)}
    for m in s.messages:
        if m.speaker is not None:
            people.setdefault(m.speaker.id, m.speaker)
    thoughts = db.query(CharacterThought).filter(CharacterThought.session_id == s.id).order_by(CharacterThought.id).all()
    standings = db.query(Standing).filter(Standing.session_id == s.id).all()
    for row in [*thoughts, *standings]:
        if row.character_id not in people and (c := db.get(Character, row.character_id)):
            people[c.id] = c

    position = {m.id: i for i, m in enumerate(s.messages)}
    worlds = {c.world.id: c.world for c in people.values() if c.world is not None}
    out = ChatFile(
        format=FORMAT, version=VERSION, exported_at=dt.datetime.now(dt.timezone.utc),
        title=s.title, created_at=s.created_at, character=s.character.name, mode=s.mode, narrator=bool(s.narrator),
        summary=s.summary or "", facts=s.facts or "", scene=s.scene, scene_prev=s.scene_prev,
        folded=sum(m.id <= (s.summarized_upto_id or 0) for m in s.messages),
        persona=PersonaOut(name=s.persona.name, description=s.persona.description or "") if s.persona else None,
        characters=[PersonOut(name=c.name, card=c.card or {}, world=c.world.name if c.world else None,
                              extra=bool(c.extra), rhythm=c.rhythm, voice=c.voice, member=c.id in members,
                              avatar=_avatar(c.avatar_path)) for c in people.values()],
        worlds=[WorldOut(name=w.name, description=w.description or "", entries=w.entries or []) for w in worlds.values()],
        messages=[LineOut(role=m.role, content=m.content, created_at=m.created_at, alternatives=m.alternatives,
                          alt_index=m.alt_index or 0, pinned=bool(m.pinned),
                          speaker=m.speaker.name if m.speaker is not None else None,
                          speaker_gone=m.speaker_id is not None and m.speaker is None,
                          initiated=bool(m.initiated), texted=bool(m.texted)) for m in s.messages],
        thoughts=[ThoughtOut(character=people[t.character_id].name, message=position.get(t.message_id),
                             journal=t.journal, view=t.view, created_at=t.created_at)
                  for t in thoughts if t.character_id in people],
        standings=[StandingOut(character=people[r.character_id].name, trust=r.trust, affection=r.affection,
                               respect=r.respect, last_reason=r.last_reason or "")
                   for r in standings if r.character_id in people],
    )
    return out.model_dump(mode="json")


def _save_avatar(data: str | None) -> str | None:
    """A PNG from the file, under a name of our choosing. Anything else is ignored."""
    if not data:
        return None
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw.startswith(PNG) or len(raw) > MAX_AVATAR:
        return None
    folder = os.path.join(settings.data_dir, "avatars")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{uuid.uuid4().hex}.png")
    with open(path, "wb") as fh:
        fh.write(raw)
    return path


def _clean_rhythm(day: dict[str, Any] | None) -> dict[str, str] | None:
    """Only what the scheduler can read: it does arithmetic on these on every status check."""
    day = day or {}
    good = {}
    for pair in (("wake", "sleep"), ("busy_from", "busy_until")):
        if all(isinstance(day.get(k), str) and _TIME.fullmatch(day[k]) for k in pair):
            good.update({k: day[k] for k in pair})
    if day.get("reach") in REACH:
        good["reach"] = day["reach"]
    return good or None


def _naive(when: dt.datetime | None) -> dt.datetime:
    """Stored as naive UTC, like every other time in the database."""
    if when is None:
        return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    return when.astimezone(dt.timezone.utc).replace(tzinfo=None) if when.tzinfo else when


def _check(data: ChatFile) -> None:
    if data.format != FORMAT:
        raise ImportRefused(f"Not a {FORMAT} file.")
    if data.version > VERSION:
        raise ImportRefused("This chat was exported by a newer version. Update this server first.")
    named = {p.name.lower() for p in data.characters}
    if data.character.lower() not in named:
        raise ImportRefused(f"The file doesn't include {data.character}.")
    for m in data.messages:
        if m.speaker and m.speaker.lower() not in named:
            raise ImportRefused(f"A message is by {m.speaker}, who isn't in the file.")
        if m.alternatives is not None and not 0 <= m.alt_index < len(m.alternatives):
            raise ImportRefused("A message points at a version it doesn't have.")
    for row in [*data.thoughts, *data.standings]:
        if row.character.lower() not in named:
            raise ImportRefused(f"The journal mentions {row.character}, who isn't in the file.")
    if not all(p.name.strip() for p in data.characters):
        raise ImportRefused("Every character needs a name.")


def import_chat(db: DbSession, data: ChatFile) -> dict:
    """A new chat from the file. Raises ImportRefused before writing anything."""
    _check(data)
    report: dict[str, Any] = {"characters_created": [], "characters_reused": [], "worlds_created": [],
                              "persona_created": None}

    worlds = {w.name.lower(): w for w in db.query(World).all()}
    in_file = {w.name.lower(): w for w in data.worlds}
    existing = {c.name.lower(): c for c in db.query(Character).order_by(Character.id).all()}
    people: dict[str, Character] = {}
    for p in data.characters:
        key = p.name.lower()
        if key in existing:
            people[key] = existing[key]
            report["characters_reused"].append(existing[key].name)
            continue
        world = None
        if p.world:
            world = worlds.get(p.world.lower())
            source = in_file.get(p.world.lower())
            if world is None and source is not None:
                world = World(name=source.name, description=source.description,
                              entries=[e.model_dump() for e in source.entries])
                db.add(world)
                db.flush()
                worlds[p.world.lower()] = world
                report["worlds_created"].append(world.name)
        try:
            card = CharacterCard.model_validate({**p.card, "name": p.name})
        except ValidationError as exc:
            raise ImportRefused(f"{p.name}'s card in the file is damaged.") from exc
        c = Character(name=card.name, card=card.model_dump(), avatar_path=_save_avatar(p.avatar),
                      world_id=world.id if world else None, extra=p.extra and world is not None,
                      rhythm=_clean_rhythm(p.rhythm), voice=p.voice)
        db.add(c)
        db.flush()
        people[key] = c
        report["characters_created"].append(c.name)

    persona = None
    if data.persona:
        persona = next((x for x in db.query(Persona).order_by(Persona.id).all()
                        if x.name.lower() == data.persona.name.lower()), None)
        if persona is None:
            persona = Persona(name=data.persona.name, description=data.persona.description)
            db.add(persona)
            db.flush()
            report["persona_created"] = persona.name

    taken = {t for (t,) in db.query(ChatSession.title).all()}
    owner = people[data.character.lower()]
    s = ChatSession(
        title=f"{data.title} (imported)" if data.title in taken else data.title,
        character_id=owner.id, persona_id=persona.id if persona else None,
        summary=data.summary, facts=data.facts, scene=data.scene, scene_prev=data.scene_prev,
        mode=data.mode, narrator=data.narrator, autonomous=False, created_at=_naive(data.created_at),
    )
    db.add(s)
    db.flush()
    position = 0
    for p in data.characters:
        c = people[p.name.lower()]
        if p.member and c.id != owner.id:
            db.add(SessionMember(session_id=s.id, character_id=c.id, position=position))
            position += 1

    ids: list[int] = []
    for m in data.messages:
        speaker = GONE if m.speaker_gone else (people[m.speaker.lower()].id if m.speaker else None)
        row = Message(session_id=s.id, role=m.role, content=m.content, created_at=_naive(m.created_at),
                      alternatives=m.alternatives, alt_index=m.alt_index, pinned=m.pinned, speaker_id=speaker,
                      initiated=m.initiated, texted=m.texted)
        db.add(row)
        db.flush()
        ids.append(row.id)
    s.summarized_upto_id = ids[min(data.folded, len(ids)) - 1] if data.folded and ids else 0

    for t in data.thoughts:
        at = t.message if t.message is not None and 0 <= t.message < len(ids) else None
        db.add(CharacterThought(session_id=s.id, character_id=people[t.character.lower()].id,
                                message_id=ids[at] if at is not None else None, journal=t.journal, view=t.view,
                                created_at=_naive(t.created_at)))
    seen = set()
    for r in data.standings:
        cid = people[r.character.lower()].id
        if cid not in seen:
            seen.add(cid)
            db.add(Standing(session_id=s.id, character_id=cid, trust=r.trust, affection=r.affection,
                            respect=r.respect, last_reason=r.last_reason))
    db.commit()
    report.update(id=s.id, title=s.title, messages=len(ids))
    return report
