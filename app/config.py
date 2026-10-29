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
    # A 7B by default, not the 13B this project was designed around. The stated
    # requirement is an 8GB card, and MythoMax 13B needs ~11GB once the KV cache
    # is counted -- so the old default could not run on the hardware the docs
    # advertised. Mistral's grouped-query attention also makes its cache six
    # times cheaper per token, so this fits with room to spare.
    # Namespaced on purpose: bare names resolve to Ollama's official library,
    # where these community models do not exist.
    model: str = "HammerAI/smart-lemon-cookie"
    # Instruct template the raw prompt is rendered in: "alpaca" | "llama3" |
    # "chatml". Must match what the *model* was trained on -- a Llama 3 tune fed
    # Alpaca still answers, but thinner, and drifts as the chat grows. Not
    # inferred from Ollama's template, which can be wrong for imported GGUFs.
    prompt_format: str = "alpaca"
    # House style appended to every character's prompt, after the card's own
    # system prompt. Blank adds nothing.
    narration_style: str = ""
    # Strip sentences narrating the user's own actions before a reply is
    # stored. On by default: prompting alone measured 4-9 such turns in 16 on
    # an 8B, and each one stored teaches the next. See chat/guard.py.
    guard_user_actions: bool = True
    # Regenerate once when a reply's narration slips into first person on a
    # card whose greeting is not. ~1 reply in 5 on Stheno, and a stored slip is
    # copied by every reply after it. Costs one extra generation when it fires.
    redo_first_person: bool = True

    # --- Context / prompt budgeting ---
    # MythoMax L2 13B is Llama-2 based: 4096 native context.
    context_tokens: int = 4096
    max_new_tokens: int = 400
    reserve_tokens: int = 256  # headroom kept free during budgeting

    # --- Default sampler params (overridable per-request/UI) ---
    temperature: float = 0.9
    top_p: float = 0.9
    top_k: int = 40
    # Drops tokens below this fraction of the top token's probability. Unlike
    # top_p it scales with the model's confidence, which is what lets a high
    # temperature stay coherent; most modern RP tunes publish settings around
    # it. 0 disables it, so existing installs sample exactly as before.
    min_p: float = 0.0
    repeat_penalty: float = 1.1
    # How far back the repeat penalty looks, in tokens. Ollama's own default is
    # 64 -- shorter than one reply here (~300 tokens), so the penalty could not
    # see the end of the sentence being written, let alone the previous turn,
    # and phrasing recycled across the chat while repeat_penalty looked set.
    # Measured on a real chat, sampling continuations of a live prompt and
    # scoring vocabulary not already used: 15.4% new at 64, 24.4% at 1024.
    # Raising repeat_penalty to 1.18 on top reached 29.7%, but that is a
    # sharper instrument -- this window is the part that was simply missing.
    repeat_last_n: int = 1024

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
    # Cosine floor, measured rather than guessed. Against nomic-embed-text,
    # genuinely relevant turns scored 0.65-0.84 while questions about events
    # that never happened still reached 0.46-0.55 -- so the earlier 0.45 passed
    # essentially everything, and every negative probe injected pure noise.
    # 0.60 sits in the observed gap. See "Verification status" in CLAUDE.md.
    retrieval_min_score: float = 0.60
    retrieval_budget_tokens: int = 400
    # How many recent *user* turns form the query. One, because more is worse:
    # measured on a real chat, "what was the innkeeper's name?" alone found the
    # message naming him at rank 1, while blending in the adjacent user turns
    # dropped it to rank 3 where a long narration hit ate the token budget and
    # pushed it out entirely. The model then invented a name. Even a bare
    # fragment ("And your father's name?") retrieved correctly on its own, so
    # the context this was meant to supply is not worth the dilution.
    retrieval_query_messages: int = 1
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
