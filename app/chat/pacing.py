"""Keeps a reply in the moment the user is in: nobody walks out, and the clock stays put.

Over 120 scripted Stheno replies with the pacing prompt on, quiet talk still went through a
whole day: Maria went evening, night, dawn, afternoon, evening. Joel kept heading out of the
door mid-conversation, and Maria told the user "Sleep well" and carried on the scene alone.
It's the same loop as narrating the user (chat/guard.py). The model copies its transcript, so
one stored exit teaches the next, and asking in the prompt hasn't held. So this is applied
before storing, and the tail of a reply is the safe thing to lose: replies already end
wherever `_whole_sentences` finds a stop.

- A reply is cut at the first sentence where someone leaves or moves the scene on, unless the
  user's own line goes somewhere or says goodbye. What comes after happens somewhere else.
- A goodbye in speech goes the same way.
- A sentence moving the time of day well away from the scene's is taken out, alone.
- "As Riley takes the notebook, Maria..." loses its opening clause, and "so Riley takes a
  stool" its tail. They write the user's action without opening a sentence with it, which is
  all chat/guard.py looks for.

Narrow on purpose. Speech that only mentions leaving survives unless it's a goodbye, and a
character who gets up, pours a drink or crosses the room is still doing something in the scene.
"""
from __future__ import annotations

import re

from .guard import _narration_spans

_USER_GOES = re.compile(
    r"\b(go|going|goes|leave|leaving|head|heading|walk|walking|outside|inside|follow|come with|"
    r"good\s?night|good\s?bye|bye|bed|sleep|later|next (morning|day)|tomorrow|get going|wake|morning|evening|night)\b",
    re.I)
_EXIT = re.compile(
    # Not "tilting her head back" or "shaking his head".
    r"\b((?<!her )(?<!his )(?<!their )(?<!the )(?<!my )(?<!your )heads? (?:out|off|home|back|outside|inside|for the|toward the door|towards the door)|"
    r"heading (?:out|off|home|back|towards? home)|"
    r"steps? (?:out|outside)\b|stepping (?:out|outside)\b|walks? (?:out|away|off)\b|walking away|"
    r"turns? and (?:leaves|exits|walks|heads|goes)|exits?\b|departs?\b|departing|"
    # Not "leaves her shoulders" or "the smile disappears".
    r"leaves? (?:the (?:room|cabin|house|office|workshop|porch|barn|kitchen|building)|him|riley|you|them|her alone)\b|"
    # Not "her gaze never leaving Riley's face".
    r"leaving (?:the (?:room|cabin|house|office|workshop|porch|barn|kitchen|building)|him|riley|you|them)\b(?!['’])|"
    r"disappears? (?:into|through|down|out|from view|behind|around)|slips? out|retreats? (?:to|into|inside|back)|strides? (?:out|off|away)|"
    r"makes? (?:his|her|their) way (?:out|to|toward|towards|back|home)|sets? off\b|pads? (?:off|away)|"
    r"(?:as )?(?:they|the two(?: of them)?|the three(?: of them)?) (?:walk|head|step|set off|make their way|turn together|continue)|"
    r"leads? (?:the way|him|her|you|them)\b|climbs? into bed|turns? in\b|side-by-side)", re.I)
_GOODBYE = re.compile(
    r"\b(sleep well|good\s?night|call it a night|turn in\b|see you (?:tomorrow|in the morning|around|later|'round)|"
    r"get some (?:rest|shut-?eye|sleep)|time for bed|best be (?:going|off)|i should (?:go|get going|head)|"
    r"(?:gotta|got to|have to) (?:go|run)\b|tomorrow'?s another day|let'?s (?:go|head|get moving|get going)|"
    r"come on,? let'?s|time to (?:go|head)|best get back)", re.I)
_TIME_WORDS = re.compile(
    r"\b(morning|afternoon|evening|night|tonight|dawn|dusk|noon|midday|midnight|sunrise|sunset|later|tomorrow|"
    r"next day|hours?|meanwhile)\b", re.I)
_SHIFTS = {
    "morning": r"first (?:pale )?light|dawn|sunrise|daybreak|morning (?:light|sun)|sun (?:rises|climbs|comes up)",
    "afternoon": r"midday sun|noon|afternoon (?:light|sun)",
    "evening": r"dusk|twilight|sunset|setting sun|sun (?:sets|dips|sinks|goes down)|fading light|evening (?:light|sky)",
    "night": r"night ?fall|night (?:has )?falls?|moon (?:rises|climbs)|stars (?:begin|come out)|midnight",
}
_SHIFT = {name: re.compile(rf"\b(?:{words})\b", re.I) for name, words in _SHIFTS.items()}
_ORDER = ["morning", "afternoon", "evening", "night"]
_BUCKET_WORDS = {
    "morning": r"dawn|morning|sunrise|daybreak", "afternoon": r"afternoon|noon|midday",
    "evening": r"evening|dusk|sunset|twilight", "night": r"night|midnight",
}
_SENTENCE_END = re.compile(r"[.!?…]+[\"”*]*\s+")


def bucket(scene_time: str) -> str | None:
    """Which part of the day a scene's time means, the last word winning: "late evening"."""
    found = [(m.start(), name) for name, words in _BUCKET_WORDS.items()
             for m in re.finditer(rf"\b(?:{words})\b", scene_time or "", re.I)]
    return max(found)[1] if found else None


def _far_apart(a: str, b: str) -> bool:
    """Neighbouring parts of the day are close enough for a long chat to drift across."""
    gap = abs(_ORDER.index(a) - _ORDER.index(b))
    return min(gap, 4 - gap) > 1


def _sentence_start(text: str, floor: int, at: int) -> int:
    start = floor
    for m in _SENTENCE_END.finditer(text, floor, at):
        start = m.end()
    return start


def _close(text: str) -> str:
    """Tidy what a cut leaves: an open *span gets closed, an empty one goes, and so does
    a quote that was only the lead-in to what's gone ("So," *she prompts...).*"""
    text = text.rstrip()
    if text.count('"') % 2:
        text += '"'
    text = re.sub(r'\s*"[^"\n]{0,40},"$', "", text).rstrip()
    text = re.sub(r"\*\s*$", "*", text)
    if text.count("*") % 2:
        head, _, tail = text.rpartition("*")
        text = f"{head}*{tail.rstrip()}*" if tail.strip() else head.rstrip()
    return re.sub(r"\*\s*\*\s*$", "", text).rstrip()


def _first_exit(text: str) -> int | None:
    """Where the reply stops being in this scene: a start of sentence, or None."""
    points = []
    for a, b in _narration_spans(text):
        m = _EXIT.search(text, a, b)
        if m:
            points.append(_sentence_start(text, a, m.start()))
    # Only a goodbye in the last part of a reply: early on, it's usually about someone else.
    for q in re.finditer(r'"[^"\n]*"|“[^”\n]*”', text):
        if q.end() >= len(text) * 0.6:
            m = _GOODBYE.search(q.group())
            if m:
                inside = _sentence_start(q.group(), 1, m.start())
                # A goodbye opening the quote takes the whole quote with it.
                points.append(q.start() + inside if inside > 1 else q.start())
    return min(points) if points else None


def _cut_time_shifts(text: str, scene_time: str) -> tuple[str, int]:
    here = bucket(scene_time)
    if not here:
        return text, 0
    cuts = []
    for a, b in _narration_spans(text):
        for name, pattern in _SHIFT.items():
            if not _far_apart(here, name):
                continue
            for m in pattern.finditer(text, a, b):
                start = _sentence_start(text, a, m.start())
                end = _SENTENCE_END.search(text, m.end(), b)
                cuts.append((start, end.end() if end else b))
    if not cuts:
        return text, 0
    out, pos = [], 0
    for x, y in sorted(cuts):
        if x > pos:
            out.append(text[pos:x])
        pos = max(pos, y)
    out.append(text[pos:])
    joined = "".join(out)
    joined = re.sub(r"\*\s*\*", "", joined)
    joined = re.sub(r"[ \t]{2,}", " ", joined)
    return re.sub(r"\n{3,}", "\n\n", joined).strip(), len(cuts)


def _cut_user_clauses(text: str, user: str) -> tuple[str, int]:
    names = sorted({user, user.split()[0]}, key=len, reverse=True) if user.strip() else []
    if not names:
        return text, 0
    alts = "|".join(re.escape(n) for n in names)
    opening = re.compile(rf"\b(?:As|While|When|After|Once)\s+(?:{alts})\s+(?:\w+ly\s+)?[a-z]+\b[^,.!?\n\"”]*,\s+(?P<next>\w)")
    trailing = re.compile(rf",\s+so\s+(?:{alts})\s+[a-z]+\b[^.!?\n\"”*]*(?P<end>[.!?…])")
    spans = _narration_spans(text)
    edits = []
    for a, b in spans:
        for m in opening.finditer(text, a, b):
            edits.append((m.start(), m.end(), m.group("next").upper()))
        for m in trailing.finditer(text, a, b):
            edits.append((m.start(), m.end(), m.group("end")))
    if not edits:
        return text, 0
    out, pos = [], 0
    for x, y, new in sorted(edits):
        out += [text[pos:x], new]
        pos = y
    out.append(text[pos:])
    return "".join(out), len(edits)


def time_may_move(user_text: str, long_gap: bool) -> bool:
    """A quiet chat keeps its hour until the user moves it or real time has passed. The tracker
    reads each reply's sunset as the time now, and the next prompt reads that back."""
    return long_gap or bool(_TIME_WORDS.search(user_text or ""))


def hold_the_moment(reply: str, user_text: str, user_name: str, scene_time: str = "") -> tuple[str, dict]:
    """(the reply as it should be stored, what was done to it)."""
    done = {"exit": 0, "time": 0, "user": 0}
    text = reply
    text, done["user"] = _cut_user_clauses(text, user_name)
    if not _USER_GOES.search(user_text or ""):
        at = _first_exit(text)
        if at is not None:
            text = _close(text[:at])
            done["exit"] = 1
        text, done["time"] = _cut_time_shifts(text, scene_time)
    return _close(text) if text != reply else reply, done
