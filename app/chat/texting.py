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

_META = re.compile(r"^\(.*\)$")


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


def mode_note(session, exclude_ids=None) -> str:
    """The switch line, when the last reply was written in the other mode."""
    texting = session.mode == "texting"
    prior = [m for m in session.messages
             if m.role == "assistant" and m.id not in (exclude_ids or ())]
    if not prior:
        return TO_TEXTING if texting else ""
    last_texted = bool(prior[-1].texted)
    if texting and not last_texted:
        return TO_TEXTING
    if not texting and last_texted:
        return TO_STORY
    return ""
