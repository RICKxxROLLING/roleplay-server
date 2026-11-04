"""The narrator: when it speaks, what it may say, and where its lines end up."""
import pytest

from app.chat import narrator
from app.config import settings

NAMES = ["Riley", "Dina"]


def char(client, name="Dina"):
    return client.post("/api/characters", json={
        "name": name, "first_mes": "Hi.", "description": f"{name} patrols the valley."}).json()["id"]


def chat(client, narrating=True, **extra):
    persona = client.post("/api/personas", json={"name": "Riley", "description": ""}).json()["id"]
    return client.post("/api/sessions", json={
        "character_id": char(client), "persona_id": persona, "narrator": narrating, **extra}).json()["id"]


def say(client, sid, text="Hello."):
    with client.stream("POST", f"/api/sessions/{sid}/messages", json={"content": text}) as r:
        return "".join(r.iter_text())


def roles(client, sid):
    return [m["role"] for m in client.get(f"/api/sessions/{sid}").json()["messages"]]


@pytest.fixture
def every_two(monkeypatch):
    monkeypatch.setattr(settings, "narrator_every", 2)


# --- what it may say ----------------------------------------------------------------


@pytest.mark.parametrize("line", [
    "Outside, the wind stirs the pines.",
    "Somewhere nearby, a dog barks twice and gives up.",
    "Across the way, Mrs. Abernathy hangs her washing.",
])
def test_quiet_lines_pass(line):
    assert narrator.judge(line, NAMES) == line


@pytest.mark.parametrize("line", [
    "Outside, a sudden bang shakes the walls.",            # alarm
    "Outside, a gunshot echoes off the hills.",            # alarm
    "Somewhere nearby, a door creaks and Riley looks up.",  # writes the user
    "Not far off, Dina's horse stamps.",                   # the cast
    "Outside, the wind makes them both shiver.",           # them, by another name
    'Across the way, a man calls "Morning!"',              # speech
    "Outside, the wind stirs and",                         # cut off by the cap
    "Outside, " + "the long wind goes on " * 20 + "and on.",
])
def test_lines_that_are_refused(line):
    assert narrator.judge(line, NAMES) == ""


def test_keeps_one_sentence_of_background_and_two_of_an_event():
    text = "Before long, Sam knocks. He wants a nail. He stays to talk."
    assert narrator.judge(text, NAMES) == "Before long, Sam knocks."
    assert narrator.judge(text, NAMES, event=True) == "Before long, Sam knocks. He wants a nail."


def test_an_event_falls_back_to_its_first_sentence():
    assert narrator.judge('Before long, Sam knocks. "Hey Dina, got a nail?"', NAMES, event=True) == "Before long, Sam knocks."
    assert narrator.judge("Before long, Sam knocks. Dina looks up.", NAMES, event=True) == "Before long, Sam knocks."
    assert narrator.judge("Before long, Dina looks up. Sam knocks.", NAMES, event=True) == ""


def test_background_that_reads_as_a_plot_hook_is_refused():
    assert narrator.judge("Across the way, a lone figure heads for the gate.", NAMES) == ""
    assert narrator.judge("Across the way, a man with a rifle crosses the yard.", NAMES) == ""


def test_an_abbreviation_does_not_end_the_sentence():
    assert narrator.judge("Across the way, Mrs.", NAMES) == ""


def test_a_line_at_the_wrong_time_of_day_is_refused():
    line = "Outside, dusk settles over the valley."
    assert narrator.judge(line, NAMES, scene_time="dawn") == ""
    assert narrator.judge(line, NAMES, scene_time="evening") == line
    assert narrator.judge(line, NAMES, scene_time="unknown") == line


def test_a_line_much_like_a_recent_one_is_refused():
    old = ["Outside, the wind stirs the pines above the fence."]
    assert narrator.judge("Outside, the wind stirs the pines over the fence.", NAMES, recent=old) == ""
    assert narrator.judge("Somewhere nearby, a dog barks twice.", NAMES, recent=old)


# --- when it speaks -----------------------------------------------------------------


def test_new_chats_follow_the_default_and_old_ones_stay_off(client, monkeypatch):
    assert client.get(f"/api/sessions/{chat(client, None)}").json()["narrator"] is False
    monkeypatch.setattr(settings, "narrator_default", True)
    assert client.get(f"/api/sessions/{chat(client, None)}").json()["narrator"] is True


def test_off_means_nothing_is_asked_of_the_model(client, llm, every_two):
    sid = chat(client, False)
    for _ in range(6):
        say(client, sid)
    assert not llm.narrator_calls and "narrator" not in roles(client, sid)


def test_it_speaks_every_few_messages_and_before_the_reply(client, llm, every_two):
    sid = chat(client)
    for _ in range(9):
        say(client, sid)
    got = roles(client, sid)
    assert 2 <= got.count("narrator") <= 5
    assert got[-1] == "assistant"
    for i, role in enumerate(got):
        if role == "narrator":
            assert got[i - 1] == "user" and got[i + 1] == "assistant"


def test_the_toggle_turns_it_off_mid_chat(client, llm, every_two):
    sid = chat(client)
    client.patch(f"/api/sessions/{sid}", json={"narrator": False})
    for _ in range(5):
        say(client, sid)
    assert "narrator" not in roles(client, sid)


def test_it_stays_out_of_a_text_thread(client, llm, every_two):
    sid = chat(client)
    client.patch(f"/api/sessions/{sid}", json={"mode": "texting"})
    for _ in range(5):
        say(client, sid)
    assert "narrator" not in roles(client, sid)
    assert client.post(f"/api/sessions/{sid}/narrate", json={}).status_code == 400


def test_a_swipe_does_not_bring_another_line(client, llm, every_two):
    sid = chat(client)
    for _ in range(4):
        say(client, sid)
    before = roles(client, sid)
    calls = len(llm.narrator_calls)
    with client.stream("POST", f"/api/sessions/{sid}/regenerate", json={}) as r:
        "".join(r.iter_text())
    assert roles(client, sid) == before and len(llm.narrator_calls) == calls


def test_a_refused_line_posts_nothing_and_the_reply_still_comes(client, llm, every_two):
    sid = chat(client)
    llm.narrator_reply = " a sudden gunshot rings out."
    for _ in range(4):
        say(client, sid)
    got = roles(client, sid)
    assert "narrator" not in got and got[-1] == "assistant"
    assert len(llm.narrator_calls) >= narrator.ATTEMPTS


def test_every_third_line_is_an_event(client, llm, every_two):
    sid = chat(client)
    while roles(client, sid).count("narrator") < 4:
        say(client, sid)
    events = ["minor and everyday" in p for p in llm.narrator_calls]
    assert events[:4] == [False, False, True, False]


def test_subjects_do_not_repeat_until_each_has_had_a_turn(client):
    from types import SimpleNamespace

    s = SimpleNamespace(id=7)
    picks = [narrator._pick(s, n, False) for n in range(len(narrator.AMBIENT))]
    assert len(set(picks)) == len(narrator.AMBIENT)
    other = [narrator._pick(SimpleNamespace(id=8), n, False) for n in range(len(narrator.AMBIENT))]
    assert picks != other


# --- where its lines end up ---------------------------------------------------------


def test_the_character_reads_it_as_someone_elses_line(client, llm, every_two):
    sid = chat(client)
    for _ in range(4):
        say(client, sid)
    line = next(m["content"] for m in client.get(f"/api/sessions/{sid}").json()["messages"]
                if m["role"] == "narrator")
    prompt = next(p for p in reversed(llm.prompts) if "Stay in character" in p and line in p)
    assert f"Narrator: {line}" in prompt
    assert f"Dina: {line}" not in prompt
    # And never told it is a member of the scene.
    assert "Also in this scene" not in prompt


def test_the_reply_stops_if_the_model_starts_writing_the_narrator(client, llm, every_two):
    sid = chat(client)
    for _ in range(4):
        say(client, sid)
    stop = client.get(f"/api/sessions/{sid}/prompt").json()["stop"]
    assert "\nNarrator:" in stop


def test_earned_trust_still_scores_the_users_line_across_a_narrator_line(client, llm, every_two):
    sid = chat(client)
    for _ in range(4):
        say(client, sid, "I brought the medicine you asked for.")
    assert "narrator" in roles(client, sid)
    counted = sum("I brought the medicine" in p for p in llm.trust_calls)
    assert counted == 4


def test_something_happens_works_with_the_narrator_off(client, llm):
    sid = chat(client, False)
    llm.narrator_reply = " Sam stops by to borrow a hammer."
    r = client.post(f"/api/sessions/{sid}/narrate", json={})
    assert r.status_code == 200
    assert r.json()["role"] == "narrator" and "Sam stops by" in r.json()["content"]
    assert roles(client, sid)[-1] == "narrator"
    # Then the character answers it, without a new user message.
    with client.stream("POST", f"/api/sessions/{sid}/respond", json={}) as s:
        "".join(s.iter_text())
    assert roles(client, sid)[-2:] == ["narrator", "assistant"]


def test_something_happens_says_so_when_nothing_usable_came(client, llm):
    sid = chat(client)
    llm.narrator_reply = " a sudden scream."
    assert client.post(f"/api/sessions/{sid}/narrate", json={}).status_code == 502
    assert "narrator" not in roles(client, sid)


def test_its_lines_can_be_edited_and_deleted(client, llm):
    sid = chat(client)
    line = client.post(f"/api/sessions/{sid}/narrate", json={"event": False}).json()
    client.patch(f"/api/sessions/{sid}/messages/{line['id']}", json={"content": "Outside, snow."})
    got = client.get(f"/api/sessions/{sid}").json()["messages"][-1]
    assert got["content"] == "Outside, snow." and got["speaker_name"] is None
    client.delete(f"/api/sessions/{sid}/messages/{line['id']}")
    assert "narrator" not in roles(client, sid)
