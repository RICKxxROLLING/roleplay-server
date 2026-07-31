"""Normalised character card schema (superset of Tavern V1 / V2)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class LoreEntry(BaseModel):
    """One `character_book` entry -- keyword-triggered world info.

    Field semantics follow the V2 spec so imported community cards behave the
    way their authors intended.
    """

    keys: list[str] = Field(default_factory=list)
    content: str = ""
    enabled: bool = True

    #: Where this sits in the final prompt ordering. Lower goes first.
    insertion_order: int = 100
    case_sensitive: bool = False

    #: When the budget is exceeded, the lowest priority is dropped first.
    priority: int = 10

    #: Requires a secondary key to also match before firing (AND instead of OR).
    selective: bool = False
    secondary_keys: list[str] = Field(default_factory=list)

    #: Always injected, regardless of keywords.
    constant: bool = False

    #: "before_char" | "after_char" -- relative to the character definition.
    position: str = "after_char"

    #: Author metadata, shown in the editor. Never sent to the model.
    name: str = ""
    comment: str = ""


class CharacterCard(BaseModel):
    name: str = "Unnamed"
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    creator_notes: str = ""
    system_prompt: str = ""
    post_history_instructions: str = ""
    alternate_greetings: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    creator: str = ""
    character_version: str = ""

    character_book: list[LoreEntry] = Field(default_factory=list)

    # Book-level settings. Kept as flat fields rather than nesting the book in
    # an object, so cards imported before Phase 3 still validate -- pydantic
    # fills these defaults for any card that predates them.
    lorebook_scan_depth: int = 4
    lorebook_token_budget: int = 400
    lorebook_recursive: bool = False
