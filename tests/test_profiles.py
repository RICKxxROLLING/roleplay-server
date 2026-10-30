"""Settings travel with the model: saved per model, presets on first use."""
from __future__ import annotations

from app.profiles import preset_for


def settings(client):
    return client.get("/api/settings").json()


def test_presets_match_specific_tunes_before_families():
    assert preset_for("stheno-v3.2:8b").values["min_p"] == 0.075
    assert preset_for("hf.co/Lewdiculous/L3-8B-Stheno-v3.2-GGUF:Q4").label.startswith("Stheno")
    assert preset_for("llama3.1:8b").values == {"prompt_format": "llama3", "context_tokens": 8192}
    assert preset_for("qwen2.5:7b").values["prompt_format"] == "chatml"
    assert preset_for("HammerAI/smart-lemon-cookie").values["prompt_format"] == "alpaca"
    assert preset_for("some-unknown-model") is None


def test_first_switch_applies_a_preset(client):
    r = client.patch("/api/settings", json={"model": "stheno-v3.2:8b"}).json()
    assert r["profile"]["source"] == "preset"
    assert r["prompt_format"] == "llama3"
    assert r["temperature"] == 1.12


def test_switching_back_restores_what_the_model_was_tuned_to(client):
    client.patch("/api/settings", json={"model": "stheno-v3.2:8b"})
    client.patch("/api/settings", json={"temperature": 1.3})
    client.patch("/api/settings", json={"model": "HammerAI/smart-lemon-cookie"})
    assert settings(client)["prompt_format"] == "alpaca"
    r = client.patch("/api/settings", json={"model": "stheno-v3.2:8b"}).json()
    assert r["profile"]["source"] == "saved"
    assert r["temperature"] == 1.3
    assert r["prompt_format"] == "llama3"


def test_the_model_being_left_keeps_its_own_values(client):
    """A patch with a new model and a new temperature must not leak the
    temperature into the old model's profile."""
    client.patch("/api/settings", json={"model": "stheno-v3.2:8b"})
    client.patch("/api/settings", json={"model": "HammerAI/smart-lemon-cookie", "temperature": 0.5})
    assert settings(client)["temperature"] == 0.5  # sent values beat the preset
    client.patch("/api/settings", json={"model": "stheno-v3.2:8b"})
    assert settings(client)["temperature"] == 1.12


def test_unknown_model_keeps_current_settings(client):
    client.patch("/api/settings", json={"temperature": 0.77})
    r = client.patch("/api/settings", json={"model": "mystery-model"}).json()
    assert r["profile"]["source"] == "none"
    assert r["temperature"] == 0.77


def test_non_model_changes_report_no_profile(client):
    assert "profile" not in client.patch("/api/settings", json={"temperature": 1.0}).json()
