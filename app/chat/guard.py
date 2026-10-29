"""Removes narration of the user's own actions from a reply before it is stored.

Prompting alone does not hold this. Measured on Stheno 8B over 16-turn chats,
identical settings scored 4 and 9 turns that narrated the user, and the spread
comes from feedback rather than noise: one "*Riley nods.*" lands in history and
the model imitates its own transcript from then on. Spelling the ban out made it
worse -- a reminder naming "nodding, smiling, laughing" took a chat to 13 of 16,
because naming a behaviour primes it. Same shape as the summary guards: the
loop cannot start if the first instance is never stored.

Deliberately narrow, because a false positive deletes the character's prose:

- Only narration is scanned. Quoted speech is never touched, and when a reply
  marks narration with asterisks, unmarked text is treated as speech too.
- Only sentences whose *subject* is the user: the name (or "You") at the start
  of a sentence, followed by a verb-like lowercase word. "Riley's hands",
  "she looks at Riley" and "as you walk in" all survive.
- A quote goes only when it is attached to a removed sentence -- directly
  before it (`"Hi," Riley says`) or directly after it with nothing attributing
  it to someone else. That is the user's own line.

Known gap: a line attributed by pronoun alone ("His tone is dry. 'Hey...'")
cannot be told apart from the character's, so it stays.
"""
from __future__ import annotations

import re

_QUOTE = re.compile(r'"[^"\n]*"|“[^”\n]*”')
# A quote ending where a removed sentence starts, allowing ", " or "*" between.
_QUOTE_BEFORE = re.compile(r'("[^"\n]*"|“[^”\n]*”)[ \t,]*\*?[ \t]*$')
# Words that follow a name without making it the acting subject.
_NOT_A_VERB = {"and", "or", "but", "too", "as", "who", "whose", "which", "that"}
# Same line only: the next paragraph opening on "She..." attributes nothing.
_ATTRIBUTION = r"[ \t]*,?[ \t]*\*?[ \t]*(?:she|he|they|it|{char}|her|his)\b"


def _subject_pattern(names: list[str]) -> re.Pattern:
    alts = "|".join(re.escape(n) for n in names if n)
    # Sentence start: beginning of the span or line, or after terminal punctuation.
    return re.compile(
        # An opening phrase is allowed ("In the dark, Riley reaches..."); a
        # subordinate clause is not ("As Riley sits, Dina..." stays).
        rf"(?:(?<=^)|(?<=[.!?…]\s)|(?<=[.!?…]))\s*(?P<s>(?:[A-Z][\w' ]{{0,40}},\s+)?"
        rf"(?:{alts})\s+(?:\w+ly\s+)?"
        rf"(?P<verb>[a-z][\w-]*)\b[^.!?…\n]*[.!?…]?)",
        re.MULTILINE,
    )


def _narration_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges that are narration rather than speech."""
    if text.count("*") >= 2:
        return [(m.start(1), m.end(1)) for m in re.finditer(r"\*([^*]+)\*", text)]
    spans, pos = [], 0
    for q in _QUOTE.finditer(text):
        spans.append((pos, q.start()))
        pos = q.end()
    spans.append((pos, len(text)))
    return spans


def _trailing_ws(text: str, i: int) -> int:
    return len(re.match(r"[ \t]*", text[i:]).group())


def _gaps(start: int, end: int, cuts: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The ranges of [start, end) not covered by `cuts`, which may overlap."""
    out, pos = [], start
    for a, b in sorted(cuts):
        if a > pos:
            out.append((pos, min(a, end)))
        pos = max(pos, b)
    if pos < end:
        out.append((pos, end))
    return out


def strip_user_actions(text: str, user_name: str, char_name: str = "") -> tuple[str, int]:
    """Return (cleaned text, user-subject sentences removed)."""
    name = (user_name or "").strip()
    if not name:
        return text, 0
    names = sorted({name, name.split()[0], "You"}, key=len, reverse=True)
    subject = _subject_pattern(names)
    attribution = re.compile(_ATTRIBUTION.format(char=re.escape(char_name or "\0")), re.I)
    starred = text.count("*") >= 2
    char_first = (char_name or "").strip().split(" ")[0]

    cuts: list[tuple[int, int]] = []
    removed = 0
    for start, end in _narration_spans(text):
        span_cuts: list[tuple[int, int]] = []
        for m in subject.finditer(text[start:end]):
            if m.group("verb") in _NOT_A_VERB:
                continue
            a, b = start + m.start("s"), start + m.end("s")
            # Swallow the following spaces, so "*Riley nods. She" -> "*She".
            span_cuts.append((a, b + _trailing_ws(text, b)))
            removed += 1
            q = _QUOTE_BEFORE.search(text[:a])
            if q:
                cuts.append((q.start(1), q.end(1) + _trailing_ws(text, q.end(1))))
            # "*Riley nods, and Dina exhales.* 'Thanks.'" -- once the sentence
            # brings the character in, the line after it is hers.
            hands_back = bool(char_first) and re.search(rf"\b{re.escape(char_first)}\b", m.group("s"))
            lead = b + re.match(r"[* \t]*\n?[* \t]*", text[b:]).end()
            q = _QUOTE.match(text, lead)
            if q and not hands_back and not attribution.match(text, q.end()):
                cuts.append((q.start(), q.end() + _trailing_ws(text, q.end())))
        if not span_cuts:
            continue
        # A span emptied entirely goes with its asterisks.
        kept = "".join(text[x:y] for x, y in _gaps(start, end, span_cuts))
        if starred and not kept.strip():
            span_cuts = [(start - 1, end + 1 + _trailing_ws(text, end + 1))]
        cuts.extend(span_cuts)

    if not cuts:
        return text, 0
    cleaned = "".join(text[x:y] for x, y in _gaps(0, len(text), cuts))
    # Only tidy what removal can leave behind. Anything broader rewrites the
    # character's own formatting -- an earlier version merged paragraphs.
    cleaned = re.sub(r"[ \t]+\*(?=[\s\"“]|$)", "*", cleaned)  # "She smiles. *"
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, removed
