"""Generated people: who gets drafted, how they are kept, and how they reach a chat."""
import random

import pytest

from app.cards import people
from conftest import sse_events

ADULT_REPLY = """
Description: {{char}} mends harness and boots in a lean-to behind the stables, and has done since the walls went up. Grey at the temples, thick through the shoulders, with a leather apron that has outlived two owners. He wants a dry roof, a little tobacco and to be left alone until supper, and he rarely gets the last.
Personality: Blunt and impatient. Talks in short sentences.
Scenario: {{user}} brings a split boot to {{char}}'s lean-to.
First message: *The awl goes through the leather with a soft pop, and {{char}} doesn't look up from the bench.* "Boot or strap, {{user}}? I've got till the light goes."
Example dialogue: <START>
{{user}}: Can you fix this today?
{{char}}: "Today, he says." *He holds out a hand for it.*"""


def world(client, name="Jackson"):
    return client.post("/api/worlds", json={"name": name, "description": "A walled town in the mountains."}).json()["id"]


def char(client, name, world_id=None, **extra):
    return client.post("/api/characters", json={"name": name, "first_mes": "Hi.", "world_id": world_id,
                                                "description": f"{name} patrols the valley.", **extra}).json()["id"]


def draft(client, wid, **body):
    r = client.post(f"/api/worlds/{wid}/people/draft", json=body)
    assert r.status_code == 200, r.text
    return sse_events(r)


@pytest.fixture
def named(llm):
    llm.draft_reply = ADULT_REPLY
    return llm


# --- who gets drafted ---------------------------------------------------------------


def test_the_server_chooses_the_name_age_job_and_temperament(client, named):
    wid = world(client)
    got = next(e for e in draft(client, wid) if e["type"] == "draft")
    seed = got["seed"]
    prompt = named.draft_calls[0]
    assert f"aged {seed['age']}" in prompt and seed["role"] in prompt and seed["mood"] in prompt
    assert prompt.rstrip().endswith("Description:") and f"Name: {got['fields']['name']}" in prompt
    assert got["fields"]["name"].split()[0] in people.WOMEN + people.MEN


def test_nothing_is_saved_by_drafting(client, named):
    wid = world(client)
    draft(client, wid)
    assert client.get("/api/characters").json() == []


def test_names_avoid_everyone_here_by_first_name_too():
    taken = {f"{n} Pike" for n in people.WOMEN[:-1] + people.MEN[:-1]}
    for seed in range(20):
        got = people.pick_seed(taken, random.Random(seed)).name.split()[0]
        assert got in (people.WOMEN[-1], people.MEN[-1])


def test_a_full_name_list_still_gives_a_new_whole_name():
    taken = {f"{f} {s}" for f in people.WOMEN + people.MEN for s in people.SURNAMES[:-1]}
    got = people.pick_seed(taken, random.Random(1)).name
    assert got.split()[0] and got not in taken


def test_a_persona_name_counts_as_taken(client, named, monkeypatch):
    wid = world(client)
    client.post("/api/personas", json={"name": "Otis", "description": ""})
    seen = {}
    real = people.stream_person

    def spy(w, **kw):
        seen.update(kw)
        return real(w, **kw)

    monkeypatch.setattr(people, "stream_person", spy)
    draft(client, wid)
    assert "Otis" in seen["taken"] and seen["user_names"] == ["Otis"]


def test_the_hint_goes_in_the_brief(client, named):
    wid = world(client)
    draft(client, wid, hint="a midwife who hates horses")
    assert "a midwife who hates horses" in named.draft_calls[0]


def test_the_cast_stays_out_of_the_card_prompt(client, named):
    wid = world(client)
    char(client, "Maria", wid)
    draft(client, wid)
    assert "Maria" not in named.draft_calls[0]


# --- adults only --------------------------------------------------------------------


def test_every_card_states_the_age(client, named):
    wid = world(client)
    got = next(e for e in draft(client, wid) if e["type"] == "draft")
    assert str(got["seed"]["age"]) in got["fields"]["description"]


@pytest.mark.parametrize("text,repeated", [
    ("Nadia, at twenty-three, keeps the books.", False),
    ("A woman in her twenties.", False),
    ("Now 23, she keeps the books.", False),
    ("She is somewhere in her 20s.", False),
    ("Nadia keeps the books.", True),
])
def test_the_age_is_only_added_when_the_card_does_not_say_it(text, repeated):
    seed = people.Seed("Nadia Pike", "a woman", 23, "", "")
    assert people._state_age(text, seed).startswith("Nadia is 23.") is repeated


def test_a_new_person_does_not_share_a_surname_while_others_are_free():
    taken = {f"Someone {s}" for s in people.SURNAMES[:-1]}
    assert people.pick_seed(taken, random.Random(3)).name.endswith(people.SURNAMES[-1])


def test_a_draft_that_reads_as_a_minor_is_refused(client, llm):
    llm.draft_reply = ADULT_REPLY.replace("Grey at the temples,", "A sixteen-year-old, grey at the temples,")
    wid = world(client)
    events = draft(client, wid)
    assert not [e for e in events if e["type"] == "draft"]
    assert [e for e in events if e["type"] == "refused"]


def test_drafts_are_never_tagged_for_romance(client, named):
    wid = world(client)
    got = next(e for e in draft(client, wid) if e["type"] == "draft")
    assert got["tags"] == [people.TAG]


# --- ties ---------------------------------------------------------------------------


def test_a_tie_is_added_to_the_description(client, named):
    wid = world(client)
    char(client, "Maria", wid)
    got = next(e for e in draft(client, wid) if e["type"] == "draft")
    assert got["tie"] == "Maria"
    assert f"knows Maria from the market" in got["fields"]["description"]
    assert "Maria" not in got["fields"]["first_mes"]


def test_a_minor_is_never_the_tie(client, named):
    wid = world(client)
    char(client, "Lev", wid, description="Lev is a 13-year-old.")
    got = next(e for e in draft(client, wid) if e["type"] == "draft")
    assert got["tie"] is None and not named.tie_calls


def test_an_extra_is_never_the_tie(client, named):
    wid = world(client)
    char(client, "Otis Boone", wid, extra=True)
    got = next(e for e in draft(client, wid) if e["type"] == "draft")
    assert got["tie"] is None


def test_a_bad_tie_is_left_out_and_the_card_still_comes(client, named):
    wid = world(client)
    char(client, "Maria", wid)
    named.tie_replies = [' the market, "friend".', " {{user}}'s old cabin.", " nowhere and never stops"]
    got = next(e for e in draft(client, wid) if e["type"] == "draft")
    assert got["tie"] is None and "knows Maria" not in got["fields"]["description"]


@pytest.mark.parametrize("raw,ok", [
    (" the market.", True),
    (" the market", False),
    (' the market, "friend".', False),
    (" Riley's cabin.", False),
    (" {{user}}.", False),
    (" the market, " + "and so on " * 40 + ".", False),
])
def test_judging_a_tie(raw, ok):
    assert bool(people.judge_tie(raw, "Otis knows Maria from", ["Riley"])) is ok


# --- keeping them -------------------------------------------------------------------


def test_an_extra_stays_off_the_main_list_until_moved(client, named):
    wid = world(client)
    cid = char(client, "Otis Boone", wid, extra=True)
    listed = {c["id"]: c for c in client.get("/api/characters").json()}
    assert listed[cid]["extra"] is True and listed[cid]["world_id"] == wid
    assert client.get(f"/api/worlds/{wid}").json()["characters"][0]["extra"] is True
    client.patch(f"/api/characters/{cid}", json={"extra": False})
    assert client.get(f"/api/characters/{cid}").json()["extra"] is False
    assert "extra" not in client.get(f"/api/characters/{cid}").json()["card"]


def test_an_extra_needs_a_world(client):
    assert client.post("/api/characters", json={"name": "Otis", "extra": True}).status_code == 400
    assert client.post("/api/characters", json={"name": "Otis", "world_id": 99}).status_code == 404


# --- reaching a chat ----------------------------------------------------------------


def chat_in(client, wid, narrating=False):
    persona = client.post("/api/personas", json={"name": "Riley", "description": ""}).json()["id"]
    return client.post("/api/sessions", json={"character_id": char(client, "Dina", wid), "persona_id": persona,
                                              "narrator": narrating}).json()["id"]


def test_a_guest_arrives_with_a_line_and_can_speak(client, llm):
    wid = world(client)
    sid = chat_in(client, wid)
    cid = char(client, "Otis Boone", wid, extra=True)
    llm.narrator_reply = " Otis Boone comes up the path with a split boot."
    r = client.post(f"/api/sessions/{sid}/guest", json={"character_id": cid})
    assert r.status_code == 200, r.text
    assert "Otis Boone comes up" in r.json()["message"]["content"]
    assert [c["name"] for c in r.json()["cast"]] == ["Dina", "Otis Boone"]
    with client.stream("POST", f"/api/sessions/{sid}/respond", json={"character_id": cid}) as s:
        "".join(s.iter_text())
    last = client.get(f"/api/sessions/{sid}").json()["messages"][-1]
    assert last["speaker_name"] == "Otis Boone"


def test_a_guest_arrives_even_if_the_model_writes_nothing_usable(client, llm):
    wid = world(client)
    sid = chat_in(client, wid)
    cid = char(client, "Otis Boone", wid, extra=True)
    llm.narrator_reply = " a sudden gunshot."
    r = client.post(f"/api/sessions/{sid}/guest", json={"character_id": cid})
    assert r.json()["message"]["content"] == "Before long, Otis Boone comes by."


def test_the_arrival_line_has_to_name_the_guest(client, llm):
    wid = world(client)
    sid = chat_in(client, wid)
    cid = char(client, "Otis Boone", wid, extra=True)
    llm.narrator_reply = " a man comes up the path."
    r = client.post(f"/api/sessions/{sid}/guest", json={"character_id": cid})
    assert "Otis Boone" in r.json()["message"]["content"]


def test_a_guest_from_elsewhere_or_already_here_is_refused(client, llm):
    wid = world(client)
    sid = chat_in(client, wid)
    other = char(client, "Stranger", world(client, "Elsewhere"))
    assert client.post(f"/api/sessions/{sid}/guest", json={"character_id": other}).status_code == 400
    own = client.get(f"/api/sessions/{sid}").json()["character_id"]
    assert client.post(f"/api/sessions/{sid}/guest", json={"character_id": own}).status_code == 400


def test_no_guests_in_a_text_thread(client, llm):
    wid = world(client)
    sid = chat_in(client, wid)
    client.patch(f"/api/sessions/{sid}", json={"mode": "texting"})
    cid = char(client, "Otis Boone", wid, extra=True)
    assert client.post(f"/api/sessions/{sid}/guest", json={"character_id": cid}).status_code == 400


def visitor_prompts(client, llm, sid):
    """Press the button until every event subject has come up, and return the prompts."""
    for _ in range(4):
        client.post(f"/api/sessions/{sid}/narrate", json={})
    return [p for p in llm.narrator_calls if "minor and everyday" in p]


def test_the_narrator_uses_a_kept_extra_as_its_visitor(client, llm):
    wid = world(client)
    sid = chat_in(client, wid)
    char(client, "Otis Boone", wid, extra=True)
    prompts = visitor_prompts(client, llm, sid)
    assert any("Otis Boone stops by" in p for p in prompts)
    assert not any("someone stops by" in p for p in prompts)
    # And they are only visiting: nobody joined the scene.
    assert len(client.get(f"/api/sessions/{sid}").json()["cast"]) == 1


def test_without_extras_the_visitor_stays_anyone(client, llm):
    wid = world(client)
    sid = chat_in(client, wid)
    assert any("someone stops by" in p for p in visitor_prompts(client, llm, sid))


def test_an_extra_already_in_the_scene_does_not_stop_by(client, llm):
    wid = world(client)
    sid = chat_in(client, wid)
    cid = char(client, "Otis Boone", wid, extra=True)
    client.post(f"/api/sessions/{sid}/guest", json={"character_id": cid})
    llm.narrator_calls.clear()
    llm.narrator_reply = None
    assert not any("Otis Boone stops by" in p for p in visitor_prompts(client, llm, sid))
