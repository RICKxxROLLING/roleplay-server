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
    return "".join(re.findall(r"[a-z0-9]", text.lower()))


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


def needs_tidying(line: str) -> bool:
    return bool(line.strip()) and '"' not in line and "“" not in line and "*" not in line and len(line) <= MAX_CHARS


async def tidy(line: str) -> str:
    """`line` with its marks added, or exactly as typed when that can't be done safely."""
    if not settings.tidy_user_lines or not needs_tidying(line):
        return line
    fmt = get_format(settings.prompt_format)
    prompt = fmt.user.format(text=PROMPT.format(line=line.strip())) + fmt.joiner + fmt.response_open
    params = GenerationParams(temperature=0.2, top_p=0.9, top_k=40, repeat_penalty=1.0,
                              context_tokens=settings.context_tokens, max_new_tokens=240,
                              stop=[*fmt.stops, "\nLine:"])
    try:
        raw = await get_client().generate(prompt, params)
    except Exception:
        return line  # Best effort: the message matters more than its marks.
    first = raw.strip().split("\n")[0].strip()
    return judge(line, first) or line
