"""Keyword-triggered lorebook (Phase 3).

Authored world facts that surface only when relevant. Complements the rolling
summary: the summary carries narrative flow, the lorebook carries canon that
must stay exact -- place names, factions, rules of magic -- and costs nothing
until its keys appear.

Matching follows the V2 `character_book` contract so imported community cards
behave as authored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..cards.models import CharacterCard, LoreEntry
from ..prompt.tokens import estimate_tokens

#: Recursion is opt-in per card; this caps it regardless, since a cycle of
#: entries referencing each other would otherwise never terminate.
MAX_RECURSION_DEPTH = 3

# Scripts written without spaces between words. Word-boundary matching is not
# just useless here, it's actively wrong: Python's \w *matches* CJK, so
# (?<!\w)書庫(?!\w) can never fire inside 彼女は書庫にいる -- the neighbouring
# kana are word characters. These always use substring matching.
_NO_WORD_BOUNDARY = re.compile(
    r"[぀-ヿ"   # hiragana, katakana
    r"㐀-䶿"    # CJK extension A
    r"一-鿿"    # CJK unified
    r"가-힯"    # hangul syllables
    r"ᄀ-ᇿ"    # hangul jamo
    r"฀-๿]"   # thai
)

# Otherwise a key is "wordlike" if it's letters/digits/spaces/apostrophes/
# hyphens -- including accented Latin and Cyrillic, which do use spaces and so
# benefit from boundaries. Anything else (punctuation, emoji) falls back too.
_WORDLIKE = re.compile(r"^[\w\s'\-]+$", re.UNICODE)


@dataclass
class LoreResult:
    before_char: list[LoreEntry]
    after_char: list[LoreEntry]
    dropped: int = 0
    used_tokens: int = 0

    @property
    def all(self) -> list[LoreEntry]:
        return [*self.before_char, *self.after_char]


def matches(key: str, haystack: str, case_sensitive: bool) -> bool:
    """Whether `key` occurs in `haystack` as a whole word.

    Word boundaries matter more than they look: a substring match would fire an
    entry keyed "art" on the word "archive", or "he" on "the". That kind of
    false positive quietly poisons the context and is miserable to debug.
    """
    key = key.strip()
    if not key:
        return False

    if not case_sensitive:
        key = key.lower()
        haystack = haystack.lower()

    if not _NO_WORD_BOUNDARY.search(key) and _WORDLIKE.match(key):
        return re.search(rf"(?<!\w){re.escape(key)}(?!\w)", haystack) is not None
    return key in haystack


def _fires(entry: LoreEntry, text: str) -> bool:
    if not entry.enabled:
        return False
    if entry.constant:
        return True
    if not entry.keys:
        return False

    if not any(matches(k, text, entry.case_sensitive) for k in entry.keys):
        return False

    # `selective` turns the secondary list into an AND requirement.
    if entry.selective and entry.secondary_keys:
        return any(matches(k, text, entry.case_sensitive) for k in entry.secondary_keys)
    return True


def select(
    card: CharacterCard,
    history: list[tuple[str, str]],
    budget_tokens: int | None = None,
) -> LoreResult:
    """Pick the entries that should be injected for this turn."""
    entries = card.character_book or []
    if not entries:
        return LoreResult([], [])

    depth = max(1, card.lorebook_scan_depth)
    budget = card.lorebook_token_budget if budget_tokens is None else budget_tokens

    scan_text = "\n".join(content for _, content in history[-depth:])

    fired: list[LoreEntry] = [e for e in entries if _fires(e, scan_text)]

    # Recursive scanning: entries can mention keys belonging to other entries.
    if card.lorebook_recursive and fired:
        seen = {id(e) for e in fired}
        for _ in range(MAX_RECURSION_DEPTH):
            extra_text = "\n".join(e.content for e in fired)
            new = [
                e for e in entries if id(e) not in seen and _fires(e, extra_text)
            ]
            if not new:
                break
            for e in new:
                seen.add(id(e))
            fired.extend(new)

    # Budget: keep highest priority first, tie-broken by insertion order.
    fired.sort(key=lambda e: (-e.priority, e.insertion_order))
    kept: list[LoreEntry] = []
    used = 0
    dropped = 0
    for e in fired:
        cost = estimate_tokens(e.content)
        if budget and used + cost > budget:
            dropped += 1
            continue
        used += cost
        kept.append(e)

    # Final prompt ordering is insertion_order, not priority.
    kept.sort(key=lambda e: e.insertion_order)

    return LoreResult(
        before_char=[e for e in kept if e.position == "before_char"],
        after_char=[e for e in kept if e.position != "before_char"],
        dropped=dropped,
        used_tokens=used,
    )


def render(entries: list[LoreEntry]) -> list[str]:
    return [e.content.strip() for e in entries if e.content.strip()]
