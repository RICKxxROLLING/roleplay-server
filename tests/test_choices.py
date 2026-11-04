"""Choose your own adventure: what's offered, what's kept, and what's never suggested."""
import pytest

from app.chat import choices
from app.db import SessionLocal
from app.db.models import ChatSession, Message

GOOD = (' "Think we\'ll spot anything tonight?"\n'
        '2. I check my rifle, making sure a round is chambered.\n'
        '3. I glance east and ask, "Any word on the convoy?"')
NAMES = ["Riley", "Dina"]


def parse(raw, romance_ok=False, texting=False):
    return choices.parse("1." + raw, NAMES, romance_ok, texting)


@pytest.fixture
def chat(client, llm, session):
    llm.choices_reply = GOOD
    client.patch(f"/api/sessions/{session}", json={"choice_mode": True})
    return session


def offer(client, sid, **body):
    r = client.post(f"/api/sessions/{sid}/choices", json=body)
    assert r.status_code == 200, r.text
    return r.json()["choices"]


# --- reading an answer --------------------------------------------------------------


def test_the_chats_own_format():
    assert parse(GOOD) == ['"Think we\'ll spot anything tonight?"',
                           "*I check my rifle, making sure a round is chambered.*",
                           '*I glance east and ask* "Any word on the convoy?"']


def test_blank_lines_between_choices_are_fine():
    assert len(parse(' "One thing to say."\n\n2. I do a second thing.\n\n3. I go somewhere else.')) == 3


def test_a_line_cut_off_by_the_cap_loses_the_cut_part():
    got = parse(' "Fine."\n2. I pick up the lantern. Then I start walking down the long road toward')
    assert got == ['"Fine."', "*I pick up the lantern.*"]


def test_a_quote_that_never_closes_is_cut_back():
    got = parse(' I look up. "You know, before we start the patrol, there')
    assert got == ["*I look up.*"]


def test_a_line_with_nothing_whole_is_dropped():
    assert parse(" I start walking down the long road toward the") == []


def test_long_lines_are_cut_to_whole_sentences():
    long = "I check the gate. " + "I walk the whole wall and count every post along it twice over. " * 6
    got = parse(" " + long)
    assert got and len(got[0]) <= choices.MAX_CHARS + 2 and got[0].endswith(".*")


def test_duplicates_and_extras_go():
    got = parse(' "Same line."\n2. "Same line."\n3. I go.. out now.\n4. I do a fourth thing.\n5. I do a fifth thing.')
    assert len(got) == 3 and got.count('"Same line."') == 1


def test_a_line_that_writes_someone_else_is_dropped():
    assert parse(" Dina says she'll take the reins.\n2. I take the reins myself.") == ["*I take the reins myself.*"]


def test_template_text_is_dropped():
    assert parse(" {{user}} says something.\n2. I take the reins myself.") == ["*I take the reins myself.*"]


def test_texts_stay_plain():
    assert parse(' "omw, be there soon."\n2. what are you doing later?', texting=True) == [
        "omw, be there soon.", "what are you doing later?"]


# --- romance ------------------------------------------------------------------------


@pytest.mark.parametrize("line", [
    "I lean in and kiss her.", "I take her hand and pull her close, my lips near hers.", '"Come to bed."',
    "I run a finger along her thigh.", '"I love you."'.replace("I love you", "I love you, you know"),
])
def test_romance_is_not_offered_unless_allowed(line):
    assert parse(" " + line) == []
    assert parse(" " + line, romance_ok=True)


def test_ordinary_lines_pass_the_filter():
    assert len(parse(' "Any word from the patrol?"\n2. I saddle the horse.\n3. I ask about the old library.')) == 3


def romance_ok_for(client, session, **patch):
    cid = client.get(f"/api/sessions/{session}").json()["character_id"]
    client.patch(f"/api/characters/{cid}", json=patch)
    with SessionLocal() as db:
        return choices._romance_ok(db.get(ChatSession, session))


def test_untagged_cards_get_no_romance(client, session):
    assert romance_ok_for(client, session, tags=[]) is False


def test_a_tagged_adult_can(client, session):
    assert romance_ok_for(client, session, tags=["romance"]) is True


def test_a_minor_never_can_even_tagged(client, session):
    assert romance_ok_for(client, session, tags=["romance"], description="She is a 15-year-old archivist.") is False


def test_one_untagged_person_in_a_group_blocks_it(client, session):
    cid = client.get(f"/api/sessions/{session}").json()["character_id"]
    client.patch(f"/api/characters/{cid}", json={"tags": ["romance"]})
    other = client.post("/api/characters", json={"name": "Tomas", "first_mes": "Hi."}).json()["id"]
    client.put(f"/api/sessions/{session}/members", json={"member_ids": [other]})
    with SessionLocal() as db:
        assert choices._romance_ok(db.get(ChatSession, session)) is False


def test_the_filter_is_used_for_real(client, llm, chat):
    llm.choices_reply = ' I lean in and kiss her.\n2. I saddle the horse.\n3. I ask about the old library.'
    got = offer(client, chat)
    assert got == ["*I saddle the horse.*", "*I ask about the old library.*"]


# --- the mode -----------------------------------------------------------------------


def test_off_for_new_chats_and_can_be_turned_on(client, llm, session):
    assert client.get(f"/api/sessions/{session}").json()["choice_mode"] is False
    client.patch(f"/api/sessions/{session}", json={"choice_mode": True})
    assert client.get(f"/api/sessions/{session}").json()["choice_mode"] is True


def test_the_same_three_come_back_without_asking_again(client, llm, chat):
    first = offer(client, chat)
    assert len(first) == 3 and offer(client, chat) == first
    assert len(llm.choice_calls) == 1


def test_other_choices_asks_again(client, llm, chat):
    offer(client, chat)
    llm.choices_reply = ' "Something else."\n2. I do another thing.\n3. I go elsewhere.'
    assert offer(client, chat, refresh=True)[0] == '"Something else."'


def test_a_new_message_means_new_choices(client, llm, chat):
    offer(client, chat)
    with client.stream("POST", f"/api/sessions/{chat}/messages", json={"content": "Hello."}) as r:
        "".join(r.iter_text())
    offer(client, chat)
    assert len(llm.choice_calls) == 2


def test_an_edited_or_swiped_reply_means_new_choices(client, llm, chat):
    offer(client, chat)
    last = client.get(f"/api/sessions/{chat}").json()["messages"][-1]
    client.patch(f"/api/sessions/{chat}/messages/{last['id']}", json={"content": "Something different now."})
    offer(client, chat)
    assert len(llm.choice_calls) == 2


def test_nothing_is_offered_when_it_is_their_turn(client, llm, chat):
    with SessionLocal() as db:
        db.add(Message(session_id=chat, role="user", content="Waiting on a reply."))
        db.commit()
    assert offer(client, chat) == [] and not llm.choice_calls


def test_nothing_usable_is_an_empty_list_after_trying_again(client, llm, chat):
    llm.choices_reply = "I can't help with that and also this has no ending"
    assert offer(client, chat) == []
    assert len(llm.choice_calls) == choices.ATTEMPTS


def test_choices_stay_out_of_the_history(client, llm, chat):
    got = offer(client, chat)
    with client.stream("POST", f"/api/sessions/{chat}/messages", json={"content": "Hello."}) as r:
        "".join(r.iter_text())
    prompt = client.get(f"/api/sessions/{chat}/prompt").json()["prompt"]
    assert got[1].strip("*") not in prompt


def test_picking_one_is_an_ordinary_message(client, llm, chat):
    pick = offer(client, chat)[1]
    with client.stream("POST", f"/api/sessions/{chat}/messages", json={"content": pick}) as r:
        "".join(r.iter_text())
    msgs = client.get(f"/api/sessions/{chat}").json()["messages"]
    assert msgs[-2]["role"] == "user" and msgs[-2]["content"] == pick


def test_the_mode_survives_export_and_import(client, llm, chat):
    data = client.get(f"/api/sessions/{chat}/export").json()
    assert data["choice_mode"] is True
    new = client.post("/api/sessions/import", json=data).json()["id"]
    assert client.get(f"/api/sessions/{new}").json()["choice_mode"] is True


def test_a_texting_chat_asks_for_texts(client, llm, chat):
    client.patch(f"/api/sessions/{chat}", json={"mode": "texting"})
    llm.choices_reply = ' "omw, be there soon."\n2. what are you up to later?\n3. did you see the storm?'
    assert offer(client, chat)[0] == "omw, be there soon."
    assert "texting each other" in llm.choice_calls[0]
