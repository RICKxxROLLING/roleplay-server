"""Single place where a backend name becomes a concrete client."""
from __future__ import annotations

from ..config import settings
from .base import LLMClient
from .ollama import OllamaClient
from .openai_compat import OpenAICompatClient

# Held explicitly rather than via lru_cache: resetting needs to close whatever
# is *currently* live, and a cache can't be inspected without building one. An
# earlier lru_cache version constructed a client purely so it could close it,
# which meant a settings save failed whenever the old config was unbuildable.
_client: LLMClient | None = None


def _build() -> LLMClient:
    backend = settings.backend.lower()
    if backend == "ollama":
        return OllamaClient(settings.llm_base_url, settings.model)
    if backend in {"openai_compat", "llamacpp", "vllm", "lmstudio"}:
        return OpenAICompatClient(
            settings.llm_base_url, settings.model, settings.llm_api_key
        )
    raise ValueError(
        f"Unknown backend {settings.backend!r}. Use 'ollama' or 'openai_compat'."
    )


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = _build()
    return _client


async def reset_client() -> None:
    """Drop the live client so the next call rebuilds from current settings.

    Required whenever model, backend or base URL changes from the UI -- those
    are captured at construction, so without this a model switch is a no-op.
    Never constructs a client; if none is live there's simply nothing to close.
    """
    global _client
    old, _client = _client, None
    if old is not None:
        try:
            await old.close()
        except Exception:
            # A failure closing the old client must not block the new config.
            pass
