"""SQLAlchemy models. Single-user local storage; SQLite is plenty."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import ForeignKey, JSON, LargeBinary, String, Text, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class ModelProfile(Base):
    """The settings a model was last used with -- see app/profiles.py.

    A new table, so `create_all` adds it to existing databases; no entry in the
    additive column migration is needed.
    """

    __tablename__ = "model_profiles"

    model: Mapped[str] = mapped_column(String(300), primary_key=True)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    # Full normalised CharacterCard, stored as JSON so the schema can evolve.
    card: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    avatar_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    # The shared world this character lives in, if any. Its lore is matched
    # alongside the card's own; the card keeps its book either way.
    world_id: Mapped[int | None] = mapped_column(ForeignKey("worlds.id"), nullable=True)

    sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )
    world: Mapped["World | None"] = relationship(back_populates="characters")


class World(Base):
    """A setting shared by several characters: a description plus lore entries.

    Without it, facts about a place (Jackson, the Unitarian Empire) had to be
    copied into every card set there, and drifted apart the moment one copy was
    edited. Entries use the card lorebook's schema and matching (lorebook.py).
    """

    __tablename__ = "worlds"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    entries: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    characters: Mapped[list[Character]] = relationship(back_populates="world")


class AppSetting(Base):
    """Runtime settings edited from the UI.

    Env vars seed the defaults on first boot; once a key lands here it wins,
    so the UI is the source of truth and changes survive restarts.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class AuthToken(Base):
    """A live login session.

    Stored as a hash of the token rather than the token itself, so a leak of
    this table cannot be replayed against the server. Deleting a row is a
    logout; deleting them all is what changing the password does.
    """

    __tablename__ = "auth_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)


class Persona(Base):
    """The *user's* side -- who you are in the scene."""

    __tablename__ = "personas"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class ChatSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), default="New chat")
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"))
    persona_id: Mapped[int | None] = mapped_column(
        ForeignKey("personas.id"), nullable=True
    )
    # Phase 2: the rolling summary of everything below the watermark.
    summary: Mapped[str] = mapped_column(Text, default="")
    # Bullet list beside the prose summary, rewritten on each fold (summarizer.py).
    facts: Mapped[str] = mapped_column(Text, default="")
    # Scene state (memory/scene.py): location, time, weather, present. The
    # previous one is kept so a swipe re-rolls from the scene before the reply
    # it replaces, not after.
    scene: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    scene_prev: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Watermark. Messages with id <= this are folded into `summary` and are no
    # longer sent verbatim. 0 means nothing has been summarised yet.
    summarized_upto_id: Mapped[int] = mapped_column(default=0)
    # Characters writing first (chat/autonomy.py): off until the owner turns it on
    # per chat. `next_check_at` spaces out re-checks after a "no".
    autonomous: Mapped[bool] = mapped_column(default=False)
    # "story" or "texting" (chat/texting.py): the same chat as prose, or as a text thread.
    mode: Mapped[str] = mapped_column(String(20), default="story")
    next_check_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    character: Mapped[Character] = relationship(back_populates="sessions")
    persona: Mapped[Persona | None] = relationship()
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )
    # Group chats: characters in the scene besides `character`, who stays the
    # chat's owner -- its card seeds the greeting, its world frames the chat.
    members: Mapped[list["SessionMember"]] = relationship(
        cascade="all, delete-orphan", order_by="SessionMember.position"
    )


class SessionMember(Base):
    """A character taking part in a group chat alongside the session's own.

    A new table, so `create_all` adds it to existing databases.
    """

    __tablename__ = "session_members"

    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), primary_key=True)
    position: Mapped[int] = mapped_column(default=0)

    character: Mapped[Character] = relationship()


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    # Swipes. Every version a regenerate produced, oldest first; `content` is
    # always the selected one, so prompt building, summaries, retrieval and the
    # guards never need to know swipes exist. None until the first regenerate.
    alternatives: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    alt_index: Mapped[int] = mapped_column(default=0)
    # Kept verbatim in the prompt even after the summary condenses it -- see
    # memory/manager.py. For moments the user does not trust a fold to keep.
    pinned: Mapped[bool] = mapped_column(default=False)
    # Which character wrote a reply. None means the session's own character,
    # so every message from before group chats reads correctly.
    speaker_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"), nullable=True)
    speaker: Mapped[Character | None] = relationship()
    # Written unprompted (chat/autonomy.py); three unanswered suspend the chat.
    initiated: Mapped[bool] = mapped_column(default=False)
    # Written in texting mode: shown as phone bubbles, and how a switch back is noticed.
    texted: Mapped[bool] = mapped_column(default=False)

    session: Mapped[ChatSession] = relationship(back_populates="messages")
    embedding: Mapped["MessageEmbedding | None"] = relationship(
        back_populates="message", cascade="all, delete-orphan", uselist=False
    )


class MessageEmbedding(Base):
    """One vector per message (Phase 4).

    Stored as a raw float32 blob rather than in a vector-index extension:
    sqlite-vec would mean a loadable SQLite extension, which isn't available in
    every Python build and can't be expressed through the additive-only
    migration helper. At single-user scale a linear scan is quick enough --
    see `memory/rag.py` for the numbers.

    `model` and `content_hash` are what make the index self-healing: a vector
    computed by a different embedding model, or from text that has since been
    edited, is not comparable and gets recomputed rather than silently returning
    wrong neighbours.
    """

    __tablename__ = "message_embeddings"

    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    # Denormalised so a search can filter by session without joining messages.
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    model: Mapped[str] = mapped_column(String(200))
    dim: Mapped[int] = mapped_column()
    content_hash: Mapped[str] = mapped_column(String(64))
    # float32, little-endian, L2-normalised at write time so scoring is a dot product.
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    message: Mapped[Message] = relationship(back_populates="embedding")


class PushSubscription(Base):
    """One device that turned notifications on (app/push.py).

    Only the endpoint is kept: pushes are sent empty, so the browser's payload
    keys (p256dh/auth) would never be used and are not stored.
    """

    __tablename__ = "push_subscriptions"

    endpoint: Mapped[str] = mapped_column(String(1000), primary_key=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Notification(Base):
    """Waiting for a device to fetch it after an empty push wakes it."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    delivered: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class CharacterThought(Base):
    """One private journal entry, written after a reply (memory/thoughts.py).

    `view` is the character's opinion of the user at that point; the newest one
    per (session, character) is what the next prompt reads. Tied to the reply
    that prompted it, so a swipe replaces its entry rather than adding another.
    """

    __tablename__ = "character_thoughts"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    journal: Mapped[str] = mapped_column(Text)
    view: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

