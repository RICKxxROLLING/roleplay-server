"""Ollama adapter. Uses /api/generate with raw=true so our Alpaca prompt is
passed through verbatim instead of being wrapped in Ollama's own template."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import GenerationParams, LLMClient


class OllamaClient(LLMClient):
    supports_pull = True

    def __init__(self, base_url: str, model: str, timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(timeout=timeout)

    async def generate_stream(
        self, prompt: str, params: GenerationParams
    ) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "raw": True,  # do not apply Ollama's chat template
            "stream": True,
            "options": {
                "temperature": params.temperature,
                "top_p": params.top_p,
                "top_k": params.top_k,
                "repeat_penalty": params.repeat_penalty,
                "repeat_last_n": params.repeat_last_n,
                # Without this Ollama uses its own default window and truncates
                # the front of anything longer -- see GenerationParams.
                "num_ctx": params.context_tokens,
                "num_predict": params.max_new_tokens,
                "stop": params.stop,
            },
        }
        async with self._client.stream(
            "POST", f"{self.base_url}/api/generate", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    raise RuntimeError(f"Ollama error: {chunk['error']}")
                piece = chunk.get("response", "")
                if piece:
                    yield piece
                if chunk.get("done"):
                    break

    async def list_models(self) -> list[str]:
        resp = await self._client.get(f"{self.base_url}/api/tags")
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]

    async def pull_model(self, name: str) -> AsyncIterator[dict]:
        """Stream a model download. Removes the need to shell into the container."""
        async with self._client.stream(
            "POST",
            f"{self.base_url}/api/pull",
            json={"model": name, "stream": True},
            # Multi-GB downloads routinely outlast the normal request timeout.
            timeout=None,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    raise RuntimeError(chunk["error"])
                yield {
                    "status": chunk.get("status", ""),
                    "completed": chunk.get("completed"),
                    "total": chunk.get("total"),
                }

    async def close(self) -> None:
        await self._client.aclose()
