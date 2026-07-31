"""Backend-agnostic LLM interface.

Design note: MythoMax L2 uses the Alpaca instruction format, not a chat template.
To keep exact control over the prompt (which is what makes SillyTavern V2 cards
"feel right"), every adapter takes a fully-rendered *raw text prompt* and hits the
backend's completion endpoint -- not /v1/chat/completions. Chat-template models can
still be served this way; the prompt builder just renders their template instead.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass
class GenerationParams:
    temperature: float = 0.9
    top_p: float = 0.9
    top_k: int = 40
    repeat_penalty: float = 1.1
    max_new_tokens: int = 400
    # Stop sequences keep the model from writing the user's side of the scene.
    stop: list[str] = field(default_factory=list)


class LLMClient(ABC):
    """Implement this once per inference engine. Nothing above this layer
    knows or cares which engine is running."""

    @abstractmethod
    async def generate_stream(
        self, prompt: str, params: GenerationParams
    ) -> AsyncIterator[str]:
        """Yield generated text incrementally (token or chunk at a time)."""
        raise NotImplementedError

    @abstractmethod
    async def list_models(self) -> list[str]:
        """Return model identifiers the backend can serve."""
        raise NotImplementedError

    #: Whether this backend can download models on demand.
    supports_pull: bool = False

    async def pull_model(self, name: str) -> AsyncIterator[dict]:
        """Download a model, yielding progress dicts.

        Only Ollama manages its own model store; llama.cpp and vLLM are pointed
        at weights that already exist on disk, so they can't implement this.
        """
        raise NotImplementedError(
            "This backend cannot download models. Place the weights on disk and "
            "point the server at them instead."
        )
        yield {}  # pragma: no cover - makes this an async generator

    async def generate(self, prompt: str, params: GenerationParams) -> str:
        """Blocking convenience wrapper. Used for side tasks like summarisation,
        where there's no user waiting on individual tokens."""
        chunks: list[str] = []
        async for piece in self.generate_stream(prompt, params):
            chunks.append(piece)
        return "".join(chunks)

    async def close(self) -> None:  # pragma: no cover - adapters may override
        return None
