"""Texting mode: the chat reimagined as a text-message thread.

A per-chat mode (`ChatSession.mode`, "story" or "texting"). In texting the
prompt asks for the words the character types (prompt/builder.py,
TEXTING_SYSTEM / TEXTING_TAIL) instead of prose, and the reply is tidied here
before it is stored.

Measured on Stheno, Dina and Garrick, 32 samples across the first text after a
switch and an ongoing thread, with and without the card's example dialogue:
no asterisk actions, no quotation marks, no lines written as the user -- the
prompt alone does it. What it left was smaller: the speaker's own label on a
later line ("Dina: hopefully not"), a meta line "(No further messages)", and
one reply cut at the token cap. Those are what `clean_texts` removes.

Long distance (`ChatSession.long_distance`, texting only) makes the thread the whole
relationship: they live far apart, so the character stops inviting the user to meet up
(prompt/builder.py, LONG_DISTANCE_*), and `drop_partings` takes off the "c u soon!" and
"night, sweet dreams" that ended replies while the user was still talking. Both are
about the same thing: a thread that keeps going instead of winding itself up.

Rules that make sense for prose are off here, deliberately:
- The first-person redo: "I'm on my way" is simply how a text reads.
- The narration guard: it would read a text like "Riley you there?" as the
  user acting. Writing the user's side is still stopped by the newline-anchored
  `\\nRiley:` stop, and any line that starts with the user's label ends the reply.
"""
from __future__ import annotations

import re

#: Shown in the tail on the first reply after a switch, so the character carries
#: on naturally from wherever the story left them.
TO_TEXTING = ("{{char}} and {{user}} have gone their separate ways since the scene above; "
              "from here on they are texting.")
TO_STORY = ("{{char}} and {{user}} are together in person again; the story picks up from "
            "their texts.")

#: Replies are a few short texts; this leaves room without inviting an essay.
MAX_NEW_TOKENS = 200

#: "(No further messages)", and a picture described instead of sent: "[Photo of Sadie Wren, smiling]",
#: written live once a text asked for one. The real picture follows the reply.
_META = re.compile(r"^\(.*\)$|^\[(?:photo|pic|picture|image|selfie|attachment|attached)\b[^\]]*\]$", re.I)


def clean_texts(text: str, char_name: str, user_name: str) -> str:
    """The texts as they would appear on a phone: one per line, no stage business."""
    char_first = (char_name or "").split(" ")[0]
    labels = {n for n in (char_name, char_first) if n}
    out: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # The user's side means the model ran on past its turn: stop there.
        if user_name and re.match(rf"{re.escape(user_name)}\s*:", line):
            break
        for label in labels:
            if line.startswith(label + ":"):
                line = line[len(label) + 1:].strip()
        line = re.sub(r"\*[^*]*\*", " ", line).replace("*", "")
        line = re.sub(r"\s{2,}", " ", line).strip()
        # A text wrapped in quotes is still just the text.
        if len(line) > 1 and line[0] in "\"“" and line[-1] in "\"”":
            line = line[1:-1].strip()
        if not line or _META.match(line):
            continue
        out.append(line)
    return "\n".join(out)


#: When a text asks for a picture and one will be sent (app/routes/pictures.py). Live, without it,
#: Sadie answered "send me a pic of the painting?" with "my phone's battery died", and the selfie
#: that followed contradicted her.
SENDS_PHOTO = ("{{user}} has asked for a photo. {{char}} takes one right now and sends it: reply with "
               "the short text that goes with the photo.")


def photo_note(session, card, exclude_ids=None) -> str:
    """SENDS_PHOTO when the user's latest text asks for a picture and one can be sent."""
    from .. import images
    from ..config import settings
    from ..memory import trust

    if session.mode != "texting" or not settings.texting_selfies or not (settings.comfyui_url or "").strip():
        return ""
    users = [m for m in session.messages if m.role == "user" and m.id not in (exclude_ids or ())]
    if not users or not images.asks_for_picture(users[-1].content) or trust.kept_platonic(card):
        return ""
    return SENDS_PHOTO


def mode_note(session, exclude_ids=None, card=None) -> str:
    """The switch line, when the last reply was written in the other mode, and the photo line."""
    photo = photo_note(session, card, exclude_ids) if card is not None else ""
    return " ".join(p for p in (_switch_note(session, exclude_ids), photo) if p)


def _switch_note(session, exclude_ids=None) -> str:
    texting = session.mode == "texting"
    prior = [m for m in session.messages
             if m.role == "assistant" and m.id not in (exclude_ids or ())]
    if not prior:
        # A thread that started as texts (no greeting): there is no scene they parted from.
        return ""
    last_texted = bool(prior[-1].texted)
    if texting and not last_texted:
        return TO_TEXTING
    if not texting and last_texted:
        return TO_STORY
    return ""


# --- keeping the thread going -------------------------------------------------------------

#: A text that only signs off: "c u soon!", "Night, sweet dreams", "ok ttyl". Anchored at both
#: ends, so "see you at 8" or "night shift again ugh" are left alone.
_PARTING = re.compile(
    r"^(?:(?:ok(?:ay)?|alright|anyway|well|cool|aw+|no worries|rest up|haha|lol)[\s,.!]*)*"
    r"(?:(?:good\s?night|g?night|nite|sleep (?:tight|well)|sweet dreams|ttyl|bye+|later|"
    r"talk (?:to (?:you|u|ya) )?(?:later|soon|tomorrow|tmrw)|gotta (?:go|run)|catch (?:you|u|ya) later|"
    r"(?:see|c) (?:you|u|ya)(?: (?:soon|later|tomorrow|tmrw))?|xo+|x+)[\s,.!~]*)+"
    r"(?:\w+)?[\s,.!~]*$",
    re.I,
)
#: The user's text winds things down, so a goodbye back is only polite.
_USER_LEAVING = re.compile(
    r"\b(?:good\s?night|g?night|nite|bye|ttyl|gotta (?:go|run)|tired|sleep|bed|talk (?:later|tomorrow|soon)|"
    r"heading (?:out|off)|later|see (?:you|u|ya)|c u)\b",
    re.I,
)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]")


def _is_parting(line: str) -> bool:
    plain = _EMOJI.sub("", line).strip()
    return bool(plain) and len(plain) <= 40 and bool(_PARTING.match(plain))


def drop_partings(text: str, user_text: str) -> str:
    """Texts that only say goodbye, when the user wasn't leaving. One text is always kept:
    a reply that is nothing but a goodbye is still the reply."""
    if _USER_LEAVING.search(user_text or ""):
        return text
    lines = [l for l in (text or "").splitlines() if l.strip()]
    kept = [l for l in lines if not _is_parting(l)]
    return "\n".join(kept or lines[:1])


def long_distance(session) -> str | None:
    """What the prompt builder takes: the owner's note on where each of them is, "" for the
    general line, or None when the chat isn't a long-distance text thread."""
    if session.mode != "texting" or not getattr(session, "long_distance", False):
        return None
    return (getattr(session, "apart", "") or "").strip()
