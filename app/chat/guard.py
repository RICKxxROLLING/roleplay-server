"""Removes narration of the user's own actions from a reply before it is stored.

Prompting alone does not hold this. Measured on Stheno 8B over 16-turn chats,
identical settings scored 4 and 9 turns that narrated the user, and the spread
comes from feedback rather than noise: one "*Riley nods.*" lands in history and
the model imitates its own transcript from then on. Spelling the ban out made it
worse -- a reminder naming "nodding, smiling, laughing" took a chat to 13 of 16,
because naming a behaviour primes it. Same shape as the summary guards: the
loop cannot start if the first instance is never stored.

Deliberately narrow, because a false positive deletes the character's prose:

- Only narration is scanned. Quoted speech is never touched, and in a paragraph
  that marks narration with asterisks, unmarked text is treated as speech too.
- Only sentences whose *subject* is the user: the name (or "You") at the start
  of a sentence, followed by a verb-like lowercase word. "Riley's hands",
  "she looks at Riley" and "as you walk in" all survive.
- A quote goes only when it is attached to a removed sentence -- directly
  before it (`"Hi," Riley says`) or directly after it with nothing attributing
  it to someone else. That is the user's own line. "Directly after" reaches
  into the next paragraph only when the removed sentence ended its own: a
  paragraph that is just a quote, after one that ended on the user acting, is
  the user speaking. Seen live in a group chat ("Riley smiled.\n\n'I'd like
  that...'"), with the quote surviving its stripped attribution.
- A sentence led by the user's voice, face or expression ("Riley's expression
  softened as he...") goes only together with an unattributed quote right after
  it -- that pair is the user speaking. Without the quote it stays: "Riley's
  eyes were kind" can be the character's view of him. Seen live on Stheno.

- A quote attributed by pronoun alone (`"Yeah, good times," he acknowledged`)
  goes only when the pronoun is the user's and not the character's, and the
  last name mentioned before it, outside speech, is the user's. Pronouns are
  read off the persona and the card (`infer_pronoun`); when either is unclear
  the rule is off. Any other name in between -- another character, a place, a
  name the guard doesn't know -- keeps the quote. Seen live in a group chat.

Known gap: a pronoun-led sentence before a quote ("He shrugs. 'Sure.'") and a
user who shares the character's pronoun still cannot be told apart, so they stay.
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


#: What a possessive-led sentence must be about to count as the user speaking.
_SPEAKING_PARTS = (
    "voice|tone|expression|face|features|eyes|gaze|lips|mouth|jaw|brow|smile|grin|words|reply"
)


def _possessive_pattern(names: list[str]) -> re.Pattern:
    alts = "|".join(re.escape(n) for n in names if n and n != "You")
    return re.compile(
        rf"(?:(?<=^)|(?<=[.!?…]\s)|(?<=[.!?…]))\s*(?P<s>(?:{alts})(?:'s|’s)\s+"
        rf"(?:\w+\s+)?(?:{_SPEAKING_PARTS})\b[^.!?…\n]*[.!?…]?)",
        re.MULTILINE,
    )


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
    """Character ranges that are narration rather than speech.

    Decided per paragraph, not per reply: models switch mid-reply, opening with
    a starred paragraph and continuing in plain prose. Seen live -- with the
    style picked once for the whole reply, `"...," Riley said` in the plain
    paragraphs was read as speech and survived.
    """
    spans = []
    for para in re.finditer(r"[^\n]+", text):
        a, b = para.span()
        line = text[a:b]
        if line.count("*") >= 2:
            spans += [(a + m.start(1), a + m.end(1)) for m in re.finditer(r"\*([^*]+)\*", line)]
            continue
        pos = a
        for q in _QUOTE.finditer(text, a, b):
            spans.append((pos, q.start()))
            pos = q.end()
        spans.append((pos, b))
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


# Subject forms only. A persona is about the user but mentions the character
# ("wants to help her rebuild her life"), and the object and possessive forms
# are where those mentions land: counting them, the live Riley persona read 3
# "he" to 2 "she" and the rule went off for him.
_SHE = re.compile(r"\b(?:she|herself)\b", re.I)
_HE = re.compile(r"\b(?:he|himself)\b", re.I)


def infer_pronoun(text: str) -> str | None:
    """"he" or "she" when a persona or card clearly uses one, else None.

    Clearly: at least two uses and three times the other. A card that names
    its character throughout, or mixes people, gives None -- and the pronoun
    rule then stays off, which is the safe direction.
    """
    she, he = len(_SHE.findall(text or "")), len(_HE.findall(text or ""))
    if she >= 2 and she >= 3 * he:
        return "she"
    if he >= 2 and he >= 3 * she:
        return "he"
    return None


# `"...," he acknowledged, his gaze fixed ahead.` -- the attribution clause to the
# end of its sentence, stopping before any further quote.
_PRONOUN_ATTRIBUTION = re.compile(r'[ \t]*,?[ \t]*\*?[ \t]*(?P<p>he|she)\s+(?P<v>[a-z][\w-]*)[^.!?…\n"“]*[.!?…]?')
# Capitalised words that can't be a person, so they don't stand between the user
# and the quote. Anything else capitalised (a place, an unknown name) does.
_NOT_A_NAME = {"She", "He", "Her", "His", "Him", "They", "Their", "The", "A", "An"}


def _last_name_before(text: str, i: int) -> str | None:
    """The last capitalised word before `i`, in this paragraph and the one
    before it, outside speech."""
    start = max(text.rfind("\n\n", 0, max(text.rfind("\n\n", 0, i), 0)), 0)
    window = _QUOTE.sub(" ", text[start:i])
    # "Dina's" is a mention of Dina.
    names = [re.sub(r"['’]s$", "", w) for w in re.findall(r"\b[A-Z][\w'’]*\b", window)]
    names = [w for w in names if w not in _NOT_A_NAME]
    return names[-1] if names else None


def _pronoun_quotes(text, names, user_pronoun, char_pronoun, char_first, attribution):
    """(cuts, lines removed) for the user's lines attributed only by their pronoun."""
    if not user_pronoun or not char_pronoun or user_pronoun == char_pronoun:
        return [], 0
    cuts, removed = [], 0
    for q in _QUOTE.finditer(text):
        m = _PRONOUN_ATTRIBUTION.match(text, q.end())
        if not m or m.group("p") != user_pronoun or m.group("v") in _NOT_A_VERB:
            continue
        last = _last_name_before(text, q.start())
        if last not in names:
            continue
        cuts.append((q.start(), m.end() + _trailing_ws(text, m.end())))
        removed += 1
        # `"Yeah," he said. "Feels like ages."` -- the line after is his too.
        more = _users_quote(text, m.end(), m.group(0), char_first, attribution)
        if more:
            cuts.append(more)
    return cuts, removed


def _users_quote(text, b, sentence, char_first, attribution) -> tuple[int, int] | None:
    """The cut for a quote right after a user sentence ending at `b`, if it is
    the user's: unattributed, and the sentence does not hand over to the
    character ("*Riley nods, and Dina exhales.* 'Thanks.'" -- that line is hers)."""
    if char_first and re.search(rf"\b{re.escape(char_first)}\b", sentence):
        return None
    # Into the next paragraph only when the sentence ended its own.
    lead = b + re.match(r"[* \t]*(?:\n[ \t]*){0,2}[* \t]*", text[b:]).end()
    q = _QUOTE.match(text, lead)
    if not q or attribution.match(text, q.end()):
        return None
    return q.start(), q.end() + _trailing_ws(text, q.end())


def strip_user_actions(
    text: str, user_name: str, char_name: str = "",
    user_pronoun: str | None = None, char_pronoun: str | None = None,
) -> tuple[str, int]:
    """Return (cleaned text, user-subject sentences removed).

    The pronouns, from `infer_pronoun`, enable the pronoun-attribution rule;
    without both it is off.
    """
    name = (user_name or "").strip()
    if not name:
        return text, 0
    names = sorted({name, name.split()[0], "You"}, key=len, reverse=True)
    subject = _subject_pattern(names)
    possessive = _possessive_pattern(names)
    attribution = re.compile(_ATTRIBUTION.format(char=re.escape(char_name or "\0")), re.I)
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
            q = _users_quote(text, b, m.group("s"), char_first, attribution)
            if q:
                cuts.append(q)
        for m in possessive.finditer(text[start:end]):
            a, b = start + m.start("s"), start + m.end("s")
            q = _users_quote(text, b, m.group("s"), char_first, attribution)
            if not q:
                continue
            span_cuts.append((a, b + _trailing_ws(text, b)))
            cuts.append(q)
            removed += 1
        if not span_cuts:
            continue
        # A span emptied entirely goes with its asterisks.
        kept = "".join(text[x:y] for x, y in _gaps(start, end, span_cuts))
        if text[start - 1 : start] == "*" and text[end : end + 1] == "*" and not kept.strip():
            span_cuts = [(start - 1, end + 1 + _trailing_ws(text, end + 1))]
        cuts.extend(span_cuts)

    pronoun_cuts, more = _pronoun_quotes(
        text, names, user_pronoun, char_pronoun, char_first, attribution
    )
    cuts.extend(pronoun_cuts)
    removed += more

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


#: First-person words in narration. A handful in a long reply can be a quirk
#: ("my God" in a thought); this many means the narrator has become the character.
# "I" only capitalised (a lowercase "i" is a typo, not a voice); the rest either
# case, since "My" opens sentences.
_FIRST_PERSON = re.compile(r"\b(?:I|(?i:me|my|mine|myself))\b")
FIRST_PERSON_LIMIT = 3


def first_person_narration(text: str) -> int:
    """First-person words in the *narration* of a reply; speech never counts."""
    return sum(len(_FIRST_PERSON.findall(text[a:b])) for a, b in _narration_spans(text))


def slipped_into_first_person(reply: str, greeting: str) -> bool:
    """Whether a reply narrates in first person when the card does not.

    The card's own greeting is the reference: a card written in first person
    ("I look up as you walk in") is left alone. Measured on Stheno with the
    Dina card: about one reply in five slips, and every reply after a stored
    slip followed it -- the narrator copies the transcript, as with narrating
    the user. Regenerating the slipped reply once keeps the loop from starting.
    """
    if first_person_narration(greeting) >= FIRST_PERSON_LIMIT:
        return False
    return first_person_narration(reply) >= FIRST_PERSON_LIMIT
