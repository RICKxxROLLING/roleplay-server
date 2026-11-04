"""Reading messages aloud: what gets read, in whose voice, and how it fails."""
from __future__ import annotations

import os
import wave
from types import SimpleNamespace as NS

import pytest

from app.chat import voices
from app.config import settings
from app.db import SessionLocal
from app.db.models import ChatSession, Message

A, B = "en_US-amy-medium", "en_GB-alan-medium"


@pytest.fixture
def synth(monkeypatch):
    """Stands in for Piper: a tenth of a second of silence per call, and what was asked."""
    calls = []

    def fake(voice, text, speed):
        calls.append((voice, text, speed))
        return 22050, b"\0\0" * 2205

    monkeypatch.setattr(voices, "_synthesise", fake)
    return calls


def seconds(data: bytes, tmp_path) -> float:
    path = tmp_path / "out.wav"
    path.write_bytes(data)
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


# --- what gets read -----------------------------------------------------------------

REPLY = '*She looks up.* "You\'re late." *She taps the ledger.* "Sit down."'


def test_dialogue_reads_only_what_is_said():
    assert voices.segments(REPLY, "dialogue", A, B) == [(A, "You're late."), (A, "Sit down.")]


def test_story_reads_narration_in_the_narrators_voice():
    assert voices.segments(REPLY, "story", A, B) == [
        (B, "She looks up."), (A, "You're late."), (B, "She taps the ledger."), (A, "Sit down.")]


def test_everything_reads_it_all_in_their_voice():
    assert voices.segments(REPLY, "everything", A, B) == [
        (A, 'She looks up. "You\'re late." She taps the ledger. "Sit down."')]


def test_curly_quotes_count():
    assert voices.segments("*Nods.* “Fine.”", "dialogue", A, B) == [(A, "Fine.")]


def test_bare_words_are_speech_and_pure_action_is_nothing():
    assert voices.segments("Fine. *She nods.* Come in.", "dialogue", A, B) == [(A, "Fine. Come in.")]
    assert voices.segments("*She nods and goes back to work.*", "dialogue", A, B) == []


def test_a_text_message_is_read_whole():
    assert voices.segments('omw *running late* "soon"', "dialogue", A, B, plain=True) == [(A, 'omw running late "soon"')]


def test_punctuation_alone_is_not_read():
    assert voices.segments('"..." *She shrugs.*', "dialogue", A, B) == []


# --- whose voice --------------------------------------------------------------------


def card(desc):
    return {"description": desc}


def test_the_default_follows_the_pronouns_on_the_card():
    for i in range(30):
        her = voices.default_for(NS(id=i, card=card("She keeps her ledger close. She is tired.")))
        him = voices.default_for(NS(id=i, card=card("He keeps his ledger close. He is tired.")))
        assert voices.VOICES[her].gender == "woman" and voices.VOICES[him].gender == "man"


def test_a_thin_card_with_no_pronouns_goes_by_other_words():
    for i in range(12):
        him = voices.default_for(NS(id=i, card=card("{{char}} is Joel's brother, a gruff, fair leader.")))
        her = voices.default_for(NS(id=i, card=card("{{char}} is the blacksmith's widow.")))
        assert voices.VOICES[him].gender == "man" and voices.VOICES[her].gender == "woman"


def test_characters_get_different_defaults():
    got = {voices.default_for(NS(id=i, card=card("She rides."))) for i in range(6)}
    assert len(got) > 3


def test_a_card_with_no_pronouns_still_gets_a_voice():
    assert voices.default_for(NS(id=4, card={})) in voices.VOICES


def test_the_owners_choice_wins_and_a_stale_one_falls_back():
    assert voices.voice_of(NS(id=1, card={}, voice=A)) == A
    assert voices.voice_of(NS(id=1, card={}, voice="gone")) in voices.VOICES


def test_setting_a_voice(client, imported):
    r = client.patch(f"/api/characters/{imported}", json={"voice": A})
    assert r.status_code == 200 and client.get(f"/api/characters/{imported}").json()["voice"] == A
    assert "voice" not in client.get(f"/api/characters/{imported}").json()["card"]
    assert client.patch(f"/api/characters/{imported}", json={"voice": "../../etc/passwd"}).status_code == 400
    client.patch(f"/api/characters/{imported}", json={"voice": ""})
    got = client.get(f"/api/characters/{imported}").json()
    assert got["voice"] is None and got["effective_voice"] in voices.VOICES


def test_a_voice_survives_export_and_import(client, session):
    cid = client.get(f"/api/sessions/{session}").json()["character_id"]
    client.patch(f"/api/characters/{cid}", json={"voice": A})
    data = client.get(f"/api/sessions/{session}/export").json()
    assert data["characters"][0]["voice"] == A
    client.delete(f"/api/characters/{cid}")
    new = client.post("/api/sessions/import", json=data).json()["id"]
    cid = client.get(f"/api/sessions/{new}").json()["character_id"]
    assert client.get(f"/api/characters/{cid}").json()["voice"] == A


# --- the endpoints ------------------------------------------------------------------


def reply_id(client, session, text='*Nods.* "Fine."'):
    with SessionLocal() as db:
        m = Message(session_id=session, role="assistant", content=text)
        db.add(m)
        db.commit()
        return m.id


def test_reading_a_message(client, session, synth, tmp_path):
    mid = reply_id(client, session)
    r = client.get(f"/api/sessions/{session}/messages/{mid}/speech")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert [c[1] for c in synth] == ["Fine."]
    assert seconds(r.content, tmp_path) == pytest.approx(0.1, abs=0.01)


def test_pieces_are_joined_with_a_pause(client, session, synth, tmp_path):
    mid = reply_id(client, session, '"One." *Pause.* "Two."')
    r = client.get(f"/api/sessions/{session}/messages/{mid}/speech")
    assert seconds(r.content, tmp_path) == pytest.approx(0.1 + voices.PAUSE_SECONDS + 0.1, abs=0.01)


def test_the_same_line_is_only_rendered_once(client, session, synth):
    mid = reply_id(client, session, '"Only once, please."')
    for _ in range(2):
        assert client.get(f"/api/sessions/{session}/messages/{mid}/speech").status_code == 200
    assert len(synth) == 1


def test_an_edited_line_is_rendered_again(client, session, synth):
    mid = reply_id(client, session, '"First wording."')
    client.get(f"/api/sessions/{session}/messages/{mid}/speech")
    client.patch(f"/api/sessions/{session}/messages/{mid}", json={"content": '"Second wording."'})
    client.get(f"/api/sessions/{session}/messages/{mid}/speech")
    assert [c[1] for c in synth] == ["First wording.", "Second wording."]


def test_nothing_to_say_is_a_plain_404(client, session, synth):
    mid = reply_id(client, session, "*She nods.*")
    r = client.get(f"/api/sessions/{session}/messages/{mid}/speech")
    assert r.status_code == 404 and "nothing to read" in r.json()["detail"].lower()
    assert not synth


def test_the_users_own_lines_are_not_read(client, session, synth):
    with SessionLocal() as db:
        m = Message(session_id=session, role="user", content='"Hello."')
        db.add(m)
        db.commit()
        mid = m.id
    assert client.get(f"/api/sessions/{session}/messages/{mid}/speech").status_code == 400


def test_a_message_from_another_chat_is_not_found(client, session, synth, imported):
    other = client.post("/api/sessions", json={"character_id": imported}).json()["id"]
    mid = reply_id(client, other)
    assert client.get(f"/api/sessions/{session}/messages/{mid}/speech").status_code == 404


def test_narrator_lines_use_the_narrators_voice(client, session, synth):
    with SessionLocal() as db:
        m = Message(session_id=session, role="narrator", content="Outside, rain ticks against tin.")
        db.add(m)
        db.commit()
        mid = m.id
    client.get(f"/api/sessions/{session}/messages/{mid}/speech")
    assert synth[0][0] == settings.voices_narrator


def test_each_speaker_in_a_group_has_their_own_voice(client, session, synth):
    other = client.post("/api/characters", json={"name": "Tomas", "first_mes": "Hi."}).json()["id"]
    client.patch(f"/api/characters/{other}", json={"voice": "en_US-joe-medium"})
    client.put(f"/api/sessions/{session}/members", json={"member_ids": [other]})
    with SessionLocal() as db:
        m = Message(session_id=session, role="assistant", content='"Tomas here."', speaker_id=other)
        db.add(m)
        db.commit()
        mid = m.id
    client.get(f"/api/sessions/{session}/messages/{mid}/speech")
    assert synth[0][0] == "en_US-joe-medium"


def test_speed_comes_from_settings_and_is_kept_in_range(client, session, synth):
    client.patch("/api/settings", json={"voices_speed": 1.2})
    mid = reply_id(client, session, '"Quick now."')
    client.get(f"/api/sessions/{session}/messages/{mid}/speech")
    assert synth[0][2] == pytest.approx(1.2)
    assert client.patch("/api/settings", json={"voices_speed": 9}).status_code == 422


def test_mode_is_a_setting_and_must_be_known(client, session, synth):
    assert client.patch("/api/settings", json={"voices_mode": "shouting"}).status_code == 422
    client.patch("/api/settings", json={"voices_mode": "story"})
    mid = reply_id(client, session, '*Nods.* "Fine."')
    client.get(f"/api/sessions/{session}/messages/{mid}/speech")
    assert [c[1] for c in synth] == ["Nods.", "Fine."]


def test_listing_voices(client):
    got = client.get("/api/voices").json()
    assert len(got["voices"]) == len(voices.VOICES)
    assert {"id", "label", "gender", "accent", "downloaded"} <= set(got["voices"][0])


def test_preview_only_for_known_voices(client, synth):
    assert client.get(f"/api/voices/{A}/preview").status_code == 200
    assert client.get("/api/voices/not-a-voice-at-all/preview").status_code == 404


def test_a_failure_says_why(client, session, monkeypatch):
    def boom(voice, text, speed):
        raise voices.VoiceUnavailable("Couldn't download the voice: offline")

    monkeypatch.setattr(voices, "_synthesise", boom)
    mid = reply_id(client, session, '"Say something new."')
    r = client.get(f"/api/sessions/{session}/messages/{mid}/speech")
    assert r.status_code == 503 and "offline" in r.json()["detail"]


def test_a_failed_download_leaves_no_partial_file(monkeypatch):
    def boom(url, path):
        with open(path, "wb") as fh:
            fh.write(b"half")
        raise OSError("connection reset")

    monkeypatch.setattr(voices.urllib.request, "urlretrieve", boom)
    voice = "en_US-norman-medium"
    with pytest.raises(voices.VoiceUnavailable):
        voices._fetch(voice)
    assert not voices.downloaded(voice)
    assert not [f for f in os.listdir(voices.folder()) if f.endswith(".part")]


def test_old_renders_are_cleared(synth, monkeypatch):
    monkeypatch.setattr(voices, "KEEP_RENDERED", 2)
    for i in range(4):
        voices._render([(A, f"Line number {i}.")], 1.0)
    assert len([f for f in os.listdir(os.path.join(voices.folder(), "rendered")) if f.endswith(".wav")]) <= 2


def test_nothing_from_chat_is_touched(client, session, synth):
    mid = reply_id(client, session)
    client.get(f"/api/sessions/{session}/messages/{mid}/speech")
    with SessionLocal() as db:
        assert db.get(ChatSession, session).messages[-1].content == '*Nods.* "Fine."'
