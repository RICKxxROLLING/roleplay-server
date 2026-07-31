"""Central configuration. Everything backend-related is swappable from here or .env."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RP_", env_file=".env", extra="ignore")

    # --- LLM backend (OpenAI-compatible or Ollama-native) ---
    # backend: "ollama" | "openai_compat"
    backend: str = "ollama"
    # For Ollama the base is typically http://localhost:11434
    # For llama.cpp/vLLM/LM Studio point at their /v1 endpoint.
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str = "not-needed-for-local"
    model: str = "mythomax"  # `ollama pull mythomax` or your GGUF-backed model tag

    # --- Context / prompt budgeting ---
    # MythoMax L2 13B is Llama-2 based: 4096 native context.
    context_tokens: int = 4096
    max_new_tokens: int = 400
    reserve_tokens: int = 256  # headroom kept free during budgeting

    # --- Default sampler params (overridable per-request/UI) ---
    temperature: float = 0.9
    top_p: float = 0.9
    top_k: int = 40
    repeat_penalty: float = 1.1

    # --- Phase 2: rolling summarization ---
    summary_enabled: bool = True
    # Fold once un-summarized history exceeds this many tokens.
    summary_trigger_tokens: int = 1800
    # Never fold the most recent N messages -- recency stays verbatim.
    keep_recent_messages: int = 8
    summary_max_tokens: int = 400
    # Low temperature: summarising is a recall task, not a creative one.
    summary_temperature: float = 0.3

    # --- Storage (container defaults; /data is a named volume) ---
    database_url: str = "sqlite:////data/roleplay.db"
    data_dir: str = "/data"

    # Built React bundle, produced by the Dockerfile's web stage.
    static_dir: str = "/app/static"

    # Empty by default: the UI is served same-origin from this app, so no CORS
    # is needed. Populate only if you host the frontend elsewhere.
    cors_origins: list[str] = []


settings = Settings()
