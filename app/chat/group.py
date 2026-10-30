"""Group chats: who is in the scene, who spoke, and who answers next.

A group chat is an ordinary session plus `SessionMember` rows. Each reply is
written by one character with that character's own card, so every prompt is
still a two-sided one from the speaker's point of view: the speaker's lines are
the assistant turns, the user's are user turns, and the other characters' lines
come in as labelled turns on the user's side ("other:Name" in history roles).
Putting them on the assistant side would hand the speaker a transcript in which
it had already written as someone else, which is the same feedback loop that
made narrating the user snowball (chat/guard.py).

Single-character chats never reach any of this: with no members every reply is
the session's character and every history role is what it always was.
"""
from __future__ import annotations

import re

from ..db.models import Character, ChatSession, Message

#: Role prefix for another character's line in a speaker's history.
OTHER = "other:"


def cast(session: ChatSession) -> list[Character]:
    """The session's character first, then the members in order."""
    out = [session.character] if session.character else []
    out += [m.character for m in session.members
            if m.character and m.character.id != session.character_id]
    return out


def is_group(session: ChatSession) -> bool:
    return len(cast(session)) > 1


def speaker_id(session: ChatSession, message: Message) -> int:
    return message.speaker_id or session.character_id


def speaker_name(session: ChatSession, message: Message, names: dict[int, str] | None = None) -> str:
    names = names if names is not None else {c.id: c.name for c in cast(session)}
    sid = speaker_id(session, message)
    if sid in names:
        return names[sid]
    # Someone who left the chat keeps their name on their lines; a deleted
    # character's lines keep their text and lose only the name.
    speaker = message.speaker
    return speaker.name if speaker is not None and speaker.id == sid else "Someone"


def history_roles(
    session: ChatSession, messages: list[Message], speaker: Character | None = None
) -> list[tuple[str, str]]:
    """[(role, content)] as `speaker` sees it -- see the module docstring."""
    me = speaker.id if speaker else session.character_id
    names = {c.id: c.name for c in cast(session)}
    out = []
    for m in messages:
        if m.role == "user" or speaker_id(session, m) == me:
            out.append((m.role, m.content))
        else:
            out.append((OTHER + speaker_name(session, m, names), m.content))
    return out


def label(role: str, char_name: str, user_name: str) -> str:
    """The speaker name for a history role."""
    if role == "user":
        return user_name
    if role.startswith(OTHER):
        return role[len(OTHER):]
    return char_name


def _mentions(text: str, name: str) -> bool:
    """Whole-word, case-insensitive, full name or first name."""
    names = {name, name.split()[0]} if name.strip() else set()
    return any(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text, re.I) for n in names)


def choose_speaker(session: ChatSession, user_text: str = "") -> Character:
    """Who answers: the character the user's message names, else whoever has
    gone longest without speaking.

    Named first because addressing someone is the user's clearest signal; the
    rotation keeps a quiet member from never getting a turn. Ties in the
    rotation go to cast order, so the session's own character opens.
    """
    people = cast(session)
    if len(people) <= 1:
        return people[0] if people else session.character
    named = [c for c in people if _mentions(user_text, c.name)]
    if len(named) == 1:
        return named[0]
    pool = named or people
    last_spoke = {c.id: -1 for c in pool}
    for i, m in enumerate(session.messages):
        if m.role == "assistant" and speaker_id(session, m) in last_spoke:
            last_spoke[speaker_id(session, m)] = i
    return min(pool, key=lambda c: last_spoke[c.id])


def by_id(session: ChatSession, character_id: int) -> Character | None:
    return next((c for c in cast(session) if c.id == character_id), None)
