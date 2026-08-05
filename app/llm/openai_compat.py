"""OpenAI-compatible adapter -- covers llama.cpp server, vLLM, TGI and LM Studio.

Targets /v1/completions (raw prompt in, text out) rather than /v1/chat/completions,
so the Alpaca template we build for MythoMax survives intact.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import GenerationParams, LLMClient


class OpenAICompatClient(LLMClient):
    def __init__(
        self, base_url: str, model: str, api_key: str = "not-needed", timeout: float = 300.0
    ):
        # Accept either ".../v1" or a bare host; normalise to include /v1 once.
        base = base_url.rstrip("/")
        self.base_url = base if base.endswith("/v1") else f"{base}/v1"
        self.model = model
        self._client = httpx.AsyncClient(
            timeout=timeout, headers={"Authorization": f"Bearer {api_key}"}
        )

    async def generate_stream(
        self, prompt: str, params: GenerationParams
    ) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": True,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "max_tokens": params.max_new_tokens,
            "stop": params.stop or None,
            # Non-standard but honoured by llama.cpp / vLLM; ignored elsewhere.
            "top_k": params.top_k,
            "repetition_penalty": params.repeat_penalty,
            "repeat_last_n": params.repeat_last_n,
        }
        async with self._client.stream(
            "POST", f"{self.base_url}/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if choices:
                    piece = choices[0].get("text", "")
                    if piece:
                        yield piece

    async def list_models(self) -> list[str]:
        resp = await self._client.get(f"{self.base_url}/models")
        resp.raise_for_status()
        return [m["id"] for m in resp.json().get("data", [])]

    async def close(self) -> None:
        await self._client.aclose()
