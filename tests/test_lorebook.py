"""Keyword lorebook matching semantics (Phase 3)."""
from __future__ import annotations

import pytest

from app.cards.models import LoreEntry
from app.memory import lorebook
from conftest import history, make_card


@pytest.mark.parametrize(
    "text,expected",
    [
        ("we found the art supplies", True),
        ("Art!", True),
        ("ART", True),
        ("the archive burned", False),  # substring match would wrongly fire
        ("smart people", False),
    ],
)
def test_matching_is_whole_word(text, expected):
    """The whole point: `art` must not fire on "archive" or "smart"."""
    c = make_card([LoreEntry(keys=["art"], content="x")])
    assert bool(lorebook.select(c, history(text)).all) is expected


def test_case_sensitive_entry():
    c = make_card([LoreEntry(keys=["Rook"], content="x", case_sensitive=True)])
    assert lorebook.select(c, history("the Rook waits")).all
    assert not lorebook.select(c, history("the rook waits")).all


def test_non_wordlike_keys_fall_back_to_substring():
    """Word boundaries are meaningless for CJK and punctuation."""
    c = make_card([LoreEntry(keys=["書庫"], content="x")])
    assert lorebook.select(c, history("彼女は書庫にいる")).all


def test_selective_requires_secondary_key():
    c = make_card(
        [LoreEntry(keys=["ledger"], secondary_keys=["Vashti"], selective=True, content="x")]
    )
    assert not lorebook.select(c, history("bring the ledger")).all
    assert lorebook.select(c, history("the Vashti ledger")).all


def test_constant_entry_always_fires():
    c = make_card([LoreEntry(keys=[], constant=True, content="It is 1721.")])
    assert lorebook.select(c, history("unrelated chatter")).all


def test_disabled_entry_never_fires():
    c = make_card([LoreEntry(keys=["archive"], content="x", enabled=False)])
    assert not lorebook.select(c, history("the archive")).all


def test_entry_without_keys_does_not_fire():
    c = make_card([LoreEntry(keys=[], content="x")])
    assert not lorebook.select(c, history("anything")).all


def test_scan_depth_limits_lookback():
    c = make_card([LoreEntry(keys=["archive"], content="x")], lorebook_scan_depth=2)
    assert lorebook.select(c, history("the archive", "b")).all
    assert not lorebook.select(c, history("the archive", "b", "c", "d")).all


def test_recursive_scanning_chains_entries():
    entries = [
        LoreEntry(keys=["archive"], content="Run by the Vashti family."),
        LoreEntry(keys=["Vashti"], content="Hereditary archivists."),
    ]
    on = make_card(list(entries), lorebook_recursive=True)
    off = make_card(list(entries), lorebook_recursive=False)
    assert len(lorebook.select(on, history("the archive")).all) == 2
    assert len(lorebook.select(off, history("the archive")).all) == 1


def test_recursion_cycle_terminates():
    """Two entries naming each other's keys must not loop forever."""
    c = make_card(
        [
            LoreEntry(keys=["alpha"], content="see beta"),
            LoreEntry(keys=["beta"], content="see alpha"),
        ],
        lorebook_recursive=True,
    )
    assert len(lorebook.select(c, history("alpha")).all) == 2


def test_budget_drops_lowest_priority_first():
    c = make_card(
        [
            LoreEntry(keys=["k"], content="LOW " * 30, priority=1, insertion_order=1),
            LoreEntry(keys=["k"], content="HIGH " * 30, priority=99, insertion_order=2),
        ],
        lorebook_token_budget=50,
    )
    result = lorebook.select(c, history("k"))
    assert [e.content.split()[0] for e in result.all] == ["HIGH"]
    assert result.dropped == 1


def test_final_order_is_insertion_order_not_priority():
    c = make_card(
        [
            LoreEntry(keys=["k"], content="SECOND", priority=99, insertion_order=20),
            LoreEntry(keys=["k"], content="FIRST", priority=1, insertion_order=10),
        ]
    )
    assert [e.content for e in lorebook.select(c, history("k")).all] == ["FIRST", "SECOND"]


def test_position_splits_entries():
    c = make_card(
        [
            LoreEntry(keys=["k"], content="B", position="before_char"),
            LoreEntry(keys=["k"], content="A", position="after_char"),
        ]
    )
    r = lorebook.select(c, history("k"))
    assert [e.content for e in r.before_char] == ["B"]
    assert [e.content for e in r.after_char] == ["A"]


def test_empty_book_is_cheap():
    assert lorebook.select(make_card([]), history("anything")).all == []
