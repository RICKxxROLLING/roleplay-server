"""Token estimation, calibrated against the backend's own counts.

Deliberately dependency-free: a real tokenizer means shipping each model's
vocab. Instead the estimate starts at a cautious ~3.6 chars/token and learns
the real ratio per model from what the backend reports after every prompt.

The fixed ratio was 35% too high for Llama 3. Measured on eight real prompts
from the live box with Stheno: 4.83-4.86 chars/token on full 8K prompts (5.1
on short ones), so a prompt the builder counted at ~7,300 tokens was really
~5,400 -- about 1,900 tokens of history left unused every turn. Ollama's
`prompt_eval_count` is the whole prompt, cached tokens included (same prompt
sent twice: 5,280 both times), so it is a dependable thing to learn from.
"""
from __future__ import annotations

#: Before a model has been measured, and after a restart. Over-counts on every
#: tokenizer seen so far, which is the safe direction.
CHARS_PER_TOKEN = 3.6

#: Learned ratios are trusted only this far: counting 5% high keeps a full
#: prompt clear of the window even when this prompt tokenises a little worse
#: than the running average.
SAFETY = 0.95
#: Smoothing for the running ratio; a prompt is mostly last turn's prompt.
_ALPHA = 0.3
#: Plausible bounds, so one odd measurement (a prompt that is mostly special
#: tokens, a backend reporting 0) cannot skew budgeting.
_BOUNDS = (2.5, 6.0)
#: Short prompts tokenise less evenly; they are not worth learning from.
_MIN_TOKENS = 200

_ratios: dict[str, float] = {}


def observe(model: str, chars: int, tokens: int | None) -> None:
    """Fold one measured prompt into `model`'s running chars/token ratio."""
    if not model or not tokens or tokens < _MIN_TOKENS:
        return
    ratio = min(max(chars / tokens, _BOUNDS[0]), _BOUNDS[1])
    prev = _ratios.get(model)
    _ratios[model] = ratio if prev is None else prev + _ALPHA * (ratio - prev)


def chars_per_token(model: str | None = None) -> tuple[float, bool]:
    """(ratio used for budgeting, whether it was measured) for `model`."""
    if model is None:
        from ..config import settings

        model = settings.model
    learned = _ratios.get(model)
    if learned is None:
        return CHARS_PER_TOKEN, False
    return learned * SAFETY, True


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return int(len(text) / chars_per_token()[0]) + 1


def reset() -> None:
    """Forget learned ratios -- for tests."""
    _ratios.clear()
