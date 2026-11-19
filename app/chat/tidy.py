"""Your typed line, with speech in "quotes" and actions in *asterisks*, before it's saved.

Typed quickly, a line reads "yeah okay ill try I smile", and the character's next prompt has no
way to tell the words said from the smile. The model proposes the marks; the server keeps them
only if every word is still there, in order. On 16 plain lines from a live Maple Hollow chat, the
model kept the words in 10 at first. The other six added things: an action nobody wrote ("*I
extend my hand for a handshake*"), a changed word ("totes"), or a dropped "and". So:
  - a segment it adds after the last of your words is cut off, which recovers the extra actions;
  - words are compared with their spaces taken out, so "lookaround" may become "look around";
  - anything else that differs keeps your line exactly as typed.
A line that already has quotes or asterisks is left alone: you've formatted it yourself.
After those, 13 of the 16 lines came out formatted and the other 3 were kept as typed. Of the 13, one
put speech in asterisks ("*Nah, just moved here this week.*") and one quoted the action as well, which
is now refused too. Known limit: speech in asterisks still gets through.
"""
from __future__ import annotations

import re

from ..config import settings
from ..llm import GenerationParams, get_client
from ..prompt.formats import get_format

PROMPT = """You format lines for a roleplay chat. Put the words the person says out loud in "double quotes" and the things they do in *asterisks*, written in first person. Keep every word exactly as written, in the same order. Only add quotes, asterisks, capital letters and punctuation.

Examples:
Line: hey whats up I lean on the counter
Formatted: "Hey, what's up?" *I lean on the counter.*

Line: I pick up the book and flip through it this one looks good
Formatted: *I pick up the book and flip through it.* "This one looks good."

Line: sure thing
Formatted: "Sure thing."

Line: I walk to the window
Formatted: *I walk to the window.*

Line: {line}
Formatted:"""

#: Long messages are left as typed: the check gets weaker and the pass slower the longer they are.
MAX_CHARS = 700
_SEGMENT = re.compile(r'"[^"]*"|\*[^*]*\*|[^"*]+')


def _letters(text: str) -> str:
    # Every letter and digit of any alphabet, so a word in Russian or with an accent is checked too.
    return "".join(re.findall(r"[^\W_]", text.lower()))


def judge(line: str, formatted: str) -> str:
    """The formatted line if it keeps every word of `line` in order, else ""."""
    want = _letters(line)
    # An unclosed mark means the model lost track of which words were speech.
    if not want or formatted.count('"') % 2 or formatted.count("*") % 2:
        return ""
    # Speech quoted inside speech means the action got quoted too, as seen:
    # "I nod and grab my coat. 'Ok, see you tomorrow then.'"
    if re.search(r"(?:^|\s)'[A-Z]", formatted):
        return ""
    kept, have = [], ""
    for seg in _SEGMENT.findall(formatted.strip()):
        if not seg.strip():
            kept.append(seg)
            continue
        if have == want:
            break  # Everything after the user's last word is the model's own addition.
        have += _letters(seg)
        kept.append(seg)
        if not want.startswith(have):
            return ""
    out = "".join(kept).strip()
    if have != want or out.count('"') % 2 or out.count("*") % 2:
        return ""
    return out


#: A sentence inside speech that is really something done: "I" and a verb of the body. Live, the
#: model quoted the whole of "maybe, i dont really know anyone in town yet though. i rub the back of
#: my neck who would we even talk to", 3 of 3 times, with or without an example of an action
#: between two bits of speech. Phrases that open speech with these verbs ("I take it", "I look
#: forward", "I stand by") are left as speech.
_ACTION = re.compile(
    r"I (?!take it\b|look forward\b|stand by\b|sit on it\b|turn \w+ down\b)"
    r"(rub|take|smile|grin|glance|look|wave|lean|nod|laugh|chuckle|sigh|sit|stand|push|pull|shrug|"
    r"head|walk|step|turn|pick|put|set|hand|reach|sip|wrap|grab|scratch|open|close|point|tap|lift|"
    r"raise|lower|hold|hug|kneel|crouch|wipe|stretch|yawn|blush|wince|frown|stare|watch|"
    # "I finish my cocoa and set the mug down", quoted live.
    r"finish|drink|eat|pour|toss|wink|nudge|shake|unlock|lock|knock|place|offer|show)\b")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _actions_out_of_speech(out: str) -> str:
    """Sentences of action inside a quote become their own *action*, between the speech."""
    parts = []
    for seg in _SEGMENT.findall(out):
        if not (seg.startswith('"') and seg.endswith('"') and len(seg) > 1):
            parts.append(seg)
            continue
        speech: list[str] = []
        for sentence in _SENTENCE.split(seg[1:-1]):
            if _ACTION.match(sentence):
                if speech:
                    parts.append('"' + " ".join(speech) + '" ')
                    speech = []
                parts.append("*" + sentence + "* ")
            else:
                speech.append(sentence)
        if speech:
            parts.append('"' + " ".join(speech) + '"')
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _capitals(out: str) -> str:
    """'i' as a word, and the first letter of each quote or action, in capitals. Letters only
    change case, so `judge` still finds every word."""
    out = re.sub(r"(?<![\w'])i(?=\b|')", "I", out)
    return re.sub(r'(^|["*]\s?|[.!?]\s)([a-z])', lambda m: m.group(1) + m.group(2).upper(), out)


def needs_tidying(line: str) -> bool:
    # One paragraph only: the answer is read from one line, so more could never pass.
    return (bool(line.strip()) and '"' not in line and "“" not in line and "*" not in line
            and "\n" not in line.strip() and len(line) <= MAX_CHARS)


async def tidy(line: str) -> str:
    """`line` with its marks added, or exactly as typed when that can't be done safely."""
    if not settings.tidy_user_lines or not needs_tidying(line):
        return line
    fmt = get_format(settings.prompt_format)
    prompt = fmt.user.format(text=PROMPT.format(line=line.strip())) + fmt.joiner + fmt.response_open
    params = GenerationParams(temperature=0.2, top_p=0.9, top_k=40, repeat_penalty=1.0,
                              context_tokens=settings.context_tokens, max_new_tokens=240,
                              stop=[*fmt.stops, "\nLine:"])
    from . import autonomy  # late: autonomy imports the orchestrator

    autonomy.ACTIVE += 1  # The writing-first scheduler waits while anything is generating.
    try:
        raw = await get_client().generate(prompt, params)
    except Exception:
        return line  # Best effort: the message matters more than its marks.
    finally:
        autonomy.ACTIVE -= 1
    first = raw.strip().split("\n")[0].strip()
    out = judge(line, first)
    if not out:
        return line
    # Re-judged after the repairs, so they can never lose or change a word.
    return judge(line, _capitals(_actions_out_of_speech(out))) or out
