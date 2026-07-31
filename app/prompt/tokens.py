"""Token estimation.

Deliberately dependency-free: a real tokenizer means shipping the model's
vocab. For budgeting we only need a safe *over*-estimate, and ~3.6 chars/token
runs slightly conservative on Llama-2 tokenizers, which is the direction we want
(better to under-fill context than blow past it).

Swap in `transformers.AutoTokenizer` here later if you want exact counts.
"""
from __future__ import annotations

CHARS_PER_TOKEN = 3.6


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN) + 1
