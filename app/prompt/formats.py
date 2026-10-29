"""Instruct formats the raw prompt can be rendered in.

The builder decides *what* goes in the prompt and in which order; a format only
decides how each block is wrapped. Keeping those apart is what lets one slot
ordering serve models trained on different templates.

The format has to match the model's training, and nothing can infer it reliably:
Ollama's own template is no help, because a GGUF pulled from Hugging Face can
arrive with the wrong one. L3-8B-Stheno-v3.2 was imported with a ChatML template
despite being a Llama 3 tune. So it is an explicit setting, defaulting to the
format the original models were built for.

No format writes a BOS token. Ollama adds one even with `raw: true` -- measured,
"hello" costs two prompt tokens and "<|begin_of_text|>hello" three -- so writing
it ourselves would double it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptFormat:
    name: str
    #: Wraps the preamble: system prompt, card, lore, examples, memory.
    system: str
    user: str
    assistant: str
    #: Wraps the post-history tail. Alpaca has no system role, so it reuses the
    #: instruction header there; chat templates get a late system turn.
    tail: str
    #: Opens the reply the model is about to write.
    response_open: str
    #: Between blocks. Alpaca relies on blank lines; chat templates carry their
    #: own delimiters and extra newlines would leak into the content.
    joiner: str
    #: Format-level stops, before the user-name stop the builder adds.
    stops: tuple[str, ...]


ALPACA = PromptFormat(
    name="alpaca",
    system="{text}",
    user="### Instruction:\n{text}",
    assistant="### Response:\n{text}",
    tail="### Instruction:\n{text}",
    response_open="### Response:\n",
    joiner="\n\n",
    stops=("### Instruction:", "</s>"),
)

LLAMA3 = PromptFormat(
    name="llama3",
    system="<|start_header_id|>system<|end_header_id|>\n\n{text}<|eot_id|>",
    user="<|start_header_id|>user<|end_header_id|>\n\n{text}<|eot_id|>",
    assistant="<|start_header_id|>assistant<|end_header_id|>\n\n{text}<|eot_id|>",
    tail="<|start_header_id|>system<|end_header_id|>\n\n{text}<|eot_id|>",
    response_open="<|start_header_id|>assistant<|end_header_id|>\n\n",
    joiner="",
    stops=("<|eot_id|>", "<|start_header_id|>"),
)

CHATML = PromptFormat(
    name="chatml",
    system="<|im_start|>system\n{text}<|im_end|>\n",
    user="<|im_start|>user\n{text}<|im_end|>\n",
    assistant="<|im_start|>assistant\n{text}<|im_end|>\n",
    tail="<|im_start|>system\n{text}<|im_end|>\n",
    response_open="<|im_start|>assistant\n",
    joiner="",
    stops=("<|im_end|>", "<|im_start|>"),
)

FORMATS: dict[str, PromptFormat] = {f.name: f for f in (ALPACA, LLAMA3, CHATML)}

#: Every marker any format emits, so cleanup can strip whichever one leaked.
ALL_MARKERS: tuple[str, ...] = (
    "### Instruction:", "### Response:", "</s>",
    "<|eot_id|>", "<|start_header_id|>", "<|end_header_id|>",
    "<|im_start|>", "<|im_end|>",
)


def get_format(name: str | None) -> PromptFormat:
    """Unknown names fall back to Alpaca rather than failing the turn."""
    return FORMATS.get((name or "").strip().lower(), ALPACA)
