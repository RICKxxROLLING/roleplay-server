"""SillyTavern V2 card import."""
from __future__ import annotations

import base64
import io
import json

import pytest

from app.cards import CardImportError, normalise
from app.cards.models import CharacterCard
from conftest import card_png


def test_import_extracts_v2_fields(client):
    r = client.post(
        "/api/characters/import", files={"file": ("c.png", card_png(), "image/png")}
    )
    assert r.status_code == 200
    card = r.json()["card"]
    assert card["name"] == "Seraphine"
    assert card["personality"] == "Dry, guarded."
    assert len(card["alternate_greetings"]) == 2


def test_v1_flat_card_still_parses():
    """V1 cards have no `data` wrapper. Plenty are still in circulation."""
    card = normalise({"name": "Old", "description": "flat", "first_mes": "hi"})
    assert card.name == "Old" and card.description == "flat"


def test_v3_shaped_payload_parses():
    card = normalise({"spec": "chara_card_v3", "data": {"name": "New"}})
    assert card.name == "New"


def test_lorebook_keys_accept_comma_string():
    """Some cards ship `keys` as a comma-separated string, not a list."""
    card = normalise(
        {
            "data": {
                "name": "X",
                "character_book": {"entries": [{"keys": "ledger, tome", "content": "c"}]},
            }
        }
    )
    assert card.character_book[0].keys == ["ledger", "tome"]


def test_book_level_settings_parsed():
    card = normalise(
        {
            "data": {
                "name": "X",
                "character_book": {
                    "scan_depth": 7,
                    "token_budget": 900,
                    "recursive_scanning": True,
                    "entries": [],
                },
            }
        }
    )
    assert card.lorebook_scan_depth == 7
    assert card.lorebook_token_budget == 900
    assert card.lorebook_recursive is True


def test_card_stored_before_phase3_still_validates():
    """Book settings are flat fields precisely so old rows keep working."""
    old = {
        "name": "Old",
        "character_book": [
            {"keys": ["archive"], "content": "c", "enabled": True, "priority": 10}
        ],
    }
    card = CharacterCard.model_validate(old)
    assert card.lorebook_scan_depth == 4
    assert card.character_book[0].position == "after_char"


def test_png_without_metadata_is_rejected(client):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG")
    buf.seek(0)
    r = client.post("/api/characters/import", files={"file": ("x.png", buf, "image/png")})
    assert r.status_code == 422


def test_non_png_is_rejected(client):
    r = client.post(
        "/api/characters/import", files={"file": ("x.txt", io.BytesIO(b"nope"), "text/plain")}
    )
    assert r.status_code == 400


def test_corrupt_metadata_raises():
    from PIL import Image, PngImagePlugin

    import tempfile, os

    meta = PngImagePlugin.PngInfo()
    meta.add_text("chara", "!!!not-base64!!!")
    path = os.path.join(tempfile.mkdtemp(), "bad.png")
    Image.new("RGB", (8, 8)).save(path, "PNG", pnginfo=meta)

    from app.cards import load_card

    with pytest.raises(CardImportError):
        load_card(path)


def test_delete_character_removes_it(client, imported):
    assert client.delete(f"/api/characters/{imported}").json()["ok"]
    assert client.get(f"/api/characters/{imported}").status_code == 404


def test_edit_card_fields(client, imported):
    r = client.patch(
        f"/api/characters/{imported}",
        json={"personality": "Warmer now.", "name": "Seraphine V"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Seraphine V"
    assert r.json()["card"]["personality"] == "Warmer now."
    # the list view reflects the rename
    assert client.get("/api/characters").json()[0]["name"] == "Seraphine V"
