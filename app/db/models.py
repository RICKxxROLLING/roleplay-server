"""SQLAlchemy models. Single-user local storage; SQLite is plenty."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import ForeignKey, JSON, String, Text, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    # Full normalised CharacterCard, stored as JSON so the schema can evolve.
    card: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    avatar_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )


class AppSetting(Base):
    """Runtime settings edited from the UI.

    Env vars seed the defaults on first boot; once a key lands here it wins,
    so the UI is the source of truth and changes survive restarts.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


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
    # Watermark. Messages with id <= this are folded into `summary` and are no
    # longer sent verbatim. 0 means nothing has been summarised yet.
    summarized_upto_id: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    character: Mapped[Character] = relationship(back_populates="sessions")
    persona: Mapped[Persona | None] = relationship()
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    session: Mapped[ChatSession] = relationship(back_populates="messages")
