"""Keeping a reply in the moment: exits, goodbyes, the clock, and clauses that move the user."""
import pytest

from app.chat import pacing
from app.config import settings
from app.db import SessionLocal
from app.db.models import ChatSession


def hold(reply, user="Tell me more.", time=""):
    return pacing.hold_the_moment(reply, user, "Riley", time)


# --- exits, as seen live ------------------------------------------------------------


@pytest.mark.parametrize("reply,ends", [
    ('"Enough of that." *With that, he steps out into the cooling evening, heading towards home.*', '"Enough of that."'),
    ('"Fine." *He gives Riley a wry grin before heading toward the barn door.* *With a nod, Joel turns and exits the cabin.*',
     '"Fine." *He gives Riley a wry grin before heading toward the barn door.*'),
    ('*She looks out.* "Cold tonight." *She leads the way, her boots crunching on gravel.*', '*She looks out.* "Cold tonight."'),
    ('"Right." *They continue walking towards the farm.* "There\'s more to it."', '"Right."'),
    ('*He sits. He sighs. He walks away from the warmth of the cabin.*', '*He sits. He sighs.*'),
])
def test_a_reply_stops_where_someone_leaves(reply, ends):
    got, done = hold(reply)
    assert got == ends and done["exit"] == 1


@pytest.mark.parametrize("reply,ends", [
    ('*She nods.* "We\'ve a lot to do. Sleep well."', '*She nods.* "We\'ve a lot to do."'),
    ('*He stands, stretching for a good while.* "You folks take care. See you \'round morning time."', '*He stands, stretching for a good while.* "You folks take care."'),
    ('*He looks over at the door for a good while now.* "Come on, let\'s head inside."', '*He looks over at the door for a good while now.*'),
])
def test_a_goodbye_in_the_last_part_goes_too(reply, ends):
    assert hold(reply)[0] == ends


@pytest.mark.parametrize("reply", [
    '*Leans her head back against the cabin wall.* "It\'s been a long one."',
    '*She tilts her head back to look at him.* "Well?"',
    '*The weight of leadership never leaves her shoulders.* "Go on."',
    '*Her smile disappears.* "Not that one."',
    '*He crosses the room and pours a drink.* "Here."',
    '*She gets up and stokes the stove.* "Cold out."',
    '"Tommy heads out at first light, every day. I don\'t go with him."',
    '"Sleep well" *is what she told him that night, years ago, and it stayed with her through all the long winters that came after it.* "Anyway."',
])
def test_what_is_still_in_the_scene_is_left_alone(reply):
    assert hold(reply) == (reply, {"exit": 0, "time": 0, "user": 0})


@pytest.mark.parametrize("user", [
    "Let's go outside.", "*I head for the door.*", "Goodnight, Dina.", "I should get some sleep.", "See you tomorrow.",
])
def test_the_user_can_end_or_move_the_scene(user):
    reply = '"Night, then." *She walks away toward the cabin.*'
    assert hold(reply, user)[0] == reply


def test_a_cut_leaves_no_loose_quote_or_asterisk():
    got, _ = hold('*She watches.* "So," *she prompts, and then she walks away into the dark.*')
    assert got == "*She watches.*"
    got, _ = hold('"You folks take care of yourselves now. See you later." *He tips his hat.*'.replace("now.", "now, all of you, every one of you, because it is a long winter."))
    assert got.count('"') % 2 == 0 and got.count("*") % 2 == 0


# --- the clock ----------------------------------------------------------------------


@pytest.mark.parametrize("text,want", [
    ("late evening", "evening"), ("later that night", "night"), ("early dawn", "morning"),
    ("Afternoon", "afternoon"), ("shortly before their session starts", None), ("", None),
])
def test_reading_a_time(text, want):
    assert pacing.bucket(text) == want


def test_a_sentence_far_from_the_scenes_time_goes_alone():
    reply = '*She gazes out at the dawn breaking over the horizon.* "Long night." *She sips her cocoa.*'
    got, done = hold(reply, time="evening")
    assert done["time"] == 1 and "dawn" not in got and "Long night" in got and "sips her cocoa" in got


def test_a_neighbouring_time_stays():
    reply = '*The sun dips behind the ridge.* "Getting dark."'
    assert hold(reply, time="afternoon")[0] == reply


def test_the_clock_stays_out_of_speech():
    reply = '"Dawn comes early up here." *She sips her cocoa.*'
    assert hold(reply, time="evening")[0] == reply


def test_with_no_known_time_nothing_is_cut():
    reply = '*Dawn breaks over the ridge.* "Morning."'
    assert hold(reply, time="")[0] == reply


@pytest.mark.parametrize("user,gap,want", [
    ("Tell me more.", False, False), ("Tell me more.", True, True),
    ("*The next morning, I knock.*".replace("next morning", "next day"), False, True), ("See you tonight.", False, True),
])
def test_when_the_time_may_move(user, gap, want):
    assert pacing.time_may_move(user, gap) is want


# --- clauses that move the user -----------------------------------------------------


def test_an_opening_clause_goes_and_the_sentence_stays():
    got, done = hold('*As Riley takes the offered notebook, Maria returns to her papers.*')
    assert got == "*Maria returns to her papers.*" and done["user"] == 1


def test_a_trailing_so_clause_goes():
    got, _ = hold('*There is no real refusal, so Riley takes a stool against the wall, giving Joel space.* "Long in Jackson?"')
    assert got == '*There is no real refusal.* "Long in Jackson?"'


def test_a_mention_of_the_user_is_not_a_clause_about_them():
    for reply in ('*At Riley\'s nod, she smiles.*', '*She looks at Riley and sighs.*', '*As the silence stretches, she sits.*'):
        assert hold(reply)[0] == reply


# --- through a real turn ------------------------------------------------------------


def say(client, sid, text="Tell me more."):
    with client.stream("POST", f"/api/sessions/{sid}/messages", json={"content": text}) as r:
        return "".join(r.iter_text())


def last(client, sid):
    return client.get(f"/api/sessions/{sid}").json()["messages"][-1]["content"]


LONG = "*She looks up from the table and pushes the lamp a little closer so she can see the page.* \"It's been a quiet one so far, and I can't say I mind that at all.\" "


def test_a_turn_is_stored_without_the_exit(client, llm, session):
    llm.replies = [LONG + "*She walks away.*"]
    say(client, session)
    got = last(client, session)
    assert "walks away" not in got and "quiet one" in got


def test_too_much_lost_goes_round_again(client, llm, session):
    llm.replies = ['*She walks away from the table without a word and into the night, leaving nothing behind her at all.*', LONG]
    body = say(client, session)
    assert '"reason": "pacing"' in body and "quiet one" in last(client, session)


def test_a_second_try_is_never_left_with_nothing(client, llm, session):
    gone = '*She walks away from the table without a word and into the night.*'
    llm.replies = [gone, gone]
    say(client, session)
    assert "walks away" in last(client, session)


def test_the_switch_turns_it_off(client, llm, session):
    client.patch("/api/settings", json={"slow_pacing": False})
    llm.replies = [LONG + "*She walks away.*"]
    say(client, session)
    assert "walks away" in last(client, session)


def test_texting_is_left_alone(client, llm, session):
    client.patch(f"/api/sessions/{session}", json={"mode": "texting"})
    llm.replies = ["heading out now, she walks away lol"]
    say(client, session)
    assert "walks away" in last(client, session)


def set_scene(session, **fields):
    with SessionLocal() as db:
        s = db.get(ChatSession, session)
        s.scene = {"location": "the archive", "time": "evening", "weather": "indoors", "present": "Seraphine, You", **fields}
        db.commit()


def scene(client, session):
    return client.get(f"/api/sessions/{session}").json()["scene"]


def test_the_clock_holds_through_a_quiet_turn(client, llm, session):
    set_scene(session)
    llm.scene_reply = " the flooded archive\nTime: dawn\nWeather: indoors\nPresent: Seraphine, You"
    say(client, session, "Tell me more.")
    assert scene(client, session)["time"] == "evening"


def test_the_user_can_move_the_clock(client, llm, session):
    set_scene(session)
    llm.scene_reply = " the flooded archive\nTime: morning\nWeather: indoors\nPresent: Seraphine, You"
    say(client, session, "*The next morning, I come back.*")
    assert scene(client, session)["time"] == "morning"


def test_a_first_time_is_taken_as_given(client, llm, session):
    set_scene(session, time="unknown")
    llm.scene_reply = " the flooded archive\nTime: dawn\nWeather: indoors\nPresent: Seraphine, You"
    say(client, session)
    assert scene(client, session)["time"] == "dawn"


def test_the_clock_is_free_with_the_switch_off(client, llm, session):
    assert settings.slow_pacing is True
    client.patch("/api/settings", json={"slow_pacing": False})
    set_scene(session)
    llm.scene_reply = " the flooded archive\nTime: dawn\nWeather: indoors\nPresent: Seraphine, You"
    say(client, session)
    assert scene(client, session)["time"] == "dawn"
