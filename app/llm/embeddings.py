"""Embedding backends (Phase 4).

Deliberately a *separate* interface from `LLMClient` rather than a method on it.
An embedding model is a different model from the chat model -- you run
`nomic-embed-text` alongside MythoMax, not instead of it -- and it can live on a
different host entirely. Folding `embed()` into `LLMClient` would have forced
every adapter to carry a second model name it mostly can't use.

Same rule as the chat layer applies: local endpoints only, no cloud.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import httpx


class Embedder(ABC):
    """One embedding backend. Returns unit-length-agnostic raw vectors --
    normalisation is the caller's business (see `memory.rag`)."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch. Order of the result matches the order of `texts`."""
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - adapters may override
        return None


class OllamaEmbedder(Embedder):
    """Ollama's native embedding endpoint.

    Prefers `/api/embed` (batched, added in Ollama 0.3) and falls back to the
    older single-item `/api/embeddings` on 404, since plenty of Unraid installs
    are pinned to an older image and a hard failure here would silently disable
    retrieval with no obvious cause.
    """

    def __init__(self, base_url: str, model: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(timeout=timeout)
        self._legacy = False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self._legacy:
            resp = await self._client.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": texts},
            )
            if resp.status_code != 404:
                resp.raise_for_status()
                data = resp.json()
                if data.get("error"):
                    raise RuntimeError(f"Ollama error: {data['error']}")
                return data["embeddings"]
            # Latch the fallback so we don't pay a 404 round-trip every batch.
            self._legacy = True

        out: list[list[float]] = []
        for text in texts:
            resp = await self._client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("error"):
                raise RuntimeError(f"Ollama error: {data['error']}")
            out.append(data["embedding"])
        return out

    async def close(self) -> None:
        await self._client.aclose()


class OpenAICompatEmbedder(Embedder):
    """/v1/embeddings -- llama.cpp server, vLLM, LM Studio, Infinity."""

    def __init__(
        self, base_url: str, model: str, api_key: str = "not-needed", timeout: float = 120.0
    ):
        base = base_url.rstrip("/")
        self.base_url = base if base.endswith("/v1") else f"{base}/v1"
        self.model = model
        self._client = httpx.AsyncClient(
            timeout=timeout, headers={"Authorization": f"Bearer {api_key}"}
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = await self._client.post(
            f"{self.base_url}/embeddings",
            json={"model": self.model, "input": texts},
        )
        resp.raise_for_status()
        rows = resp.json().get("data", [])
        # The spec permits returning these out of order; `index` is authoritative.
        rows.sort(key=lambda r: r.get("index", 0))
        return [r["embedding"] for r in rows]

    async def close(self) -> None:
        await self._client.aclose()
