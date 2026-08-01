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
    # Namespaced on purpose: there is no bare `mythomax` in Ollama's official
    # library, so `ollama pull mythomax` fails with "file does not exist".
    # MythoMax only exists under community namespaces.
    model: str = "HammerAI/mythomax-l2"

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

    # --- Phase 4: vector retrieval ---
    # Off by default. It needs a second model pulled (`ollama pull nomic-embed-text`),
    # and enabling it before that exists would add a failing HTTP round-trip to
    # every turn. The Memory panel turns it on once the model is present.
    retrieval_enabled: bool = False
    embedding_model: str = "nomic-embed-text"
    # Blank means "same host as the chat model", which is the normal case.
    # Set it only if embeddings run on a separate server.
    embedding_base_url: str = ""
    retrieval_top_k: int = 4
    # Cosine floor. Below this a hit is noise, and irrelevant retrieved context
    # is worse than none -- it reads as plausible and derails the scene.
    retrieval_min_score: float = 0.45
    retrieval_budget_tokens: int = 400
    # How many recent messages form the query. More context, blurrier query.
    retrieval_query_messages: int = 3
    # Cap on vectors scanned per search. Scoring is pure Python (see rag.py);
    # this keeps the worst case bounded on a very long chat.
    retrieval_max_candidates: int = 2000

    # --- Storage (container defaults; /data is a named volume) ---
    database_url: str = "sqlite:////data/roleplay.db"
    data_dir: str = "/data"

    # Built React bundle, produced by the Dockerfile's web stage.
    static_dir: str = "/app/static"

    # Empty by default: the UI is served same-origin from this app, so no CORS
    # is needed. Populate only if you host the frontend elsewhere.
    cors_origins: list[str] = []


settings = Settings()
