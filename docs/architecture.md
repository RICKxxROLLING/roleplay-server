# Local AI Roleplay Server — Architecture & Roadmap

A design document for a fully local, self-hosted AI chat roleplay server.

**Target environment:** NVIDIA GPU, single machine, local-only. 8GB runs a 7B; the 13B default
needs ~11GB once the KV cache is counted — see [`../README.md`](../README.md#choosing-a-model).
**Decided so far:** backend-agnostic LLM layer · SillyTavern V2 card compatibility · streaming web UI · character personas · long-term memory via the hybrid of §5 Route C.

---

## 1. Goals & Non-Goals

**Goals**

- Run entirely on your own hardware. No external API calls, no telemetry, no cloud dependency for inference.
- Talk to the character through a browser with live token streaming.
- Import the existing library of SillyTavern/TavernAI V2 character cards (PNG-embedded JSON).
- Give characters memory that survives a long conversation and multiple sessions.
- Keep the LLM backend swappable so you can move between Ollama, llama.cpp, vLLM, etc. without rewriting the app.

**Non-goals (for v1)**

- Multi-user accounts and auth (single-user local; can add later).
- Fine-tuning or training models (we consume models, not train them).
- Mobile-native apps (the web UI is responsive; that's enough).
- Image generation / TTS (nice future add-ons, not core).

---

## 2. Recommended Stack

| Layer | Choice | Why |
|---|---|---|
| Server | **Python + FastAPI** | Best ML ecosystem, native async for streaming (SSE/WebSocket), trivial to bolt on embeddings/RAG later. |
| LLM access | **OpenAI-compatible client** against a local endpoint | Ollama, llama.cpp `server`, vLLM, TGI, and LM Studio all expose an OpenAI-compatible `/v1/chat/completions`. Code to that interface once; swap backends via config. |
| Default backend | **Ollama** to start | One install, GPU auto-detected, easy model pulls. Move to llama.cpp/vLLM later for finer sampler control or throughput. |
| Storage | **SQLite** (via SQLAlchemy) | Zero-config, single file, perfect for local single-user. Migrate to Postgres only if you add multi-user. |
| Vector store (if RAG) | **sqlite-vec** or **Chroma** | sqlite-vec keeps everything in the one SQLite file; Chroma is easier if you want a batteries-included store. |
| Frontend | **Vite + React + Tailwind**, or plain HTML+HTMX | React if you want a rich SillyTavern-like UI; HTMX if you want minimal JS and server-rendered simplicity. |
| Streaming | **Server-Sent Events (SSE)** | Simpler than WebSocket for one-way token streams; native browser support; easy to proxy. Use WebSocket only if you later need bidirectional (e.g. interrupt/regenerate signalling). |

**The key architectural decision** is the backend-agnostic LLM layer: a single `LLMClient` interface with a `generate_stream(messages, params)` method. Concrete adapters (`OllamaClient`, `LlamaCppClient`, `OpenAICompatClient`) implement it. Everything above that layer — prompt building, memory, cards — never knows which engine is running.

---

## 3. High-Level Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     Browser (Web UI)                       │
│   chat view · character picker · settings · memory panel   │
└───────────────▲───────────────────────┬───────────────────┘
                │ SSE (token stream)      │ REST (CRUD)
                │                         ▼
┌───────────────┴───────────────────────────────────────────┐
│                     FastAPI Server                          │
│                                                             │
│  Routes ──► Chat Orchestrator ──► Prompt Builder            │
│                    │                     ▲                  │
│                    │                     │ context          │
│                    ▼                     │                  │
│              Memory Manager ─────────────┘                  │
│              (summary / RAG / lorebook)                     │
│                    │                                        │
│  Card Loader   Session Store        LLMClient (interface)   │
│      │              │                     │                 │
└──────┼──────────────┼─────────────────────┼────────────────┘
       ▼              ▼                     ▼
   V2 PNG cards    SQLite DB        Local inference engine
                                    (Ollama / llama.cpp / vLLM)
```

**Request flow for one message:**

1. UI POSTs the user turn to `/chat/{session_id}` and opens an SSE stream.
2. Orchestrator loads the session, character card, and persona.
3. Memory Manager assembles context: system prompt + character definition + relevant memories/lorebook + recent turns.
4. Prompt Builder formats everything to fit the model's context window (token budgeting).
5. `LLMClient.generate_stream()` streams tokens back; server relays them over SSE.
6. On completion, the turn is persisted and the Memory Manager updates (summarize / embed) as needed.

---

## 4. Character Cards (SillyTavern V2)

The V2 spec stores card JSON inside a PNG's `tEXt`/`chara` metadata chunk (base64-encoded). You'll need:

- **Importer:** read the PNG, extract the `chara` chunk, base64-decode, parse JSON. Libraries like `Pillow` + a small parser handle this; the JSON schema is `spec: "chara_card_v2"` with fields `name`, `description`, `personality`, `scenario`, `first_mes`, `mes_example`, `system_prompt`, `post_history_instructions`, `alternate_greetings`, `character_book` (embedded lorebook), and `tags`.
- **Internal model:** store the normalized card in SQLite. Keep the original PNG for portability/export.
- **Prompt assembly:** V2 defines where each field goes (description + personality + scenario into the character block; `mes_example` as few-shot; `system_prompt` overrides default; `post_history_instructions` injected after chat history). Respecting this ordering is what makes community cards "feel right."
- **`character_book`:** the card's embedded lorebook — keyed entries injected when their keywords appear. This ties directly into the memory design below.

Getting V2 parsing + prompt-slot ordering correct is the single biggest compatibility win — it unlocks thousands of existing cards on day one.

---

## 5. Long-Term Memory — The Three Routes (pros & cons)

This is the hardest part and the one you were unsure about. Here's an honest breakdown.

### Route A — Rolling Summarization

Keep the last N turns verbatim. When the conversation grows past a threshold, send the oldest turns to the LLM and ask it to compress them into a running summary paragraph, which is injected near the top of context. Repeat as the chat grows.

**Pros**

- Simplest to build — no embedding model, no vector DB, no extra infrastructure.
- Cheap at runtime; one extra LLM call only occasionally (when you cross the threshold).
- Naturally preserves narrative flow and emotional arc, since the summary is prose the model wrote.
- Deterministic context size — you always know roughly how many tokens memory costs.

**Cons**

- Lossy and irreversible. Once a detail is summarized away, it's gone unless it made the summary.
- Poor at precise recall ("what was the innkeeper's name in chapter 1?") — summaries blur specifics.
- Summary quality depends on the model; small local models sometimes drop or hallucinate details.
- Doesn't scale to a large static world/lore — it only compresses *conversation*, not reference facts.

**Best when:** you mostly care about continuity of an evolving story rather than exact fact retrieval.

### Route B — Vector RAG Retrieval

Embed every message (and lorebook entries) into vectors. Each turn, embed the recent context, do a similarity search, and inject the top-k most relevant past chunks into the prompt.

**Pros**

- Excellent precise recall — surfaces the exact old message about the innkeeper when it's relevant again.
- Scales to huge histories and large static lore; retrieval cost is roughly constant regardless of size.
- Nothing is destroyed — full history stays queryable; you inject only what's relevant.
- Same machinery serves both chat memory and a world/lore knowledge base.

**Cons**

- More infrastructure: an embedding model (another GPU/CPU load), a vector store, chunking logic.
- Retrieval can miss or misfire — semantic search sometimes pulls irrelevant chunks or misses paraphrases, and injected snippets can feel disjointed out of context.
- Tuning burden: chunk size, k, similarity thresholds, and how to blend retrieved text into the prompt all need iteration.
- Doesn't inherently preserve narrative *flow* — it retrieves fragments, not a coherent recap.

**Best when:** long-running characters, large worlds, or when exact continuity of facts matters.

### Route C — Hybrid (Summary + RAG + Keyword Lorebook)

Combine all three: a rolling summary for recent narrative continuity, vector RAG over full history for precise recall, and keyword-triggered lorebook entries (SillyTavern's `character_book` model) for authored world facts. The Memory Manager blends these into a token budget each turn.

**Pros**

- Best overall quality — flow *and* precise recall *and* authored canon.
- Directly matches how SillyTavern cards expect to work (their lorebooks are keyword-triggered), so imported cards behave correctly.
- Graceful degradation — if RAG misfires, the summary still carries continuity, and vice versa.

**Cons**

- Most complex to build and tune; all three subsystems plus a blending/budgeting policy.
- Hardest to debug when the character "forgets" something — the failure could be in any of three layers.
- Highest runtime cost (embedding + occasional summarization + retrieval each turn).

**Best when:** you want the real SillyTavern-grade experience and are willing to build it in stages.

### My recommendation

**Build toward C, but ship in order A → keyword lorebook → RAG.** Start with rolling summarization (Phase 2) because it's simple and immediately useful. Add the keyword-triggered lorebook next (Phase 3) since V2 cards ship with `character_book` and it's low-cost, high-impact. Add vector RAG last (Phase 4) once the rest is stable. This way each phase is usable on its own and you never build infrastructure you can't yet exercise.

---

## 6. API Design (sketch)

```
POST   /characters/import        # upload V2 PNG, parse, store
GET    /characters               # list
GET    /characters/{id}          # detail
POST   /personas                 # define the *user's* persona
POST   /sessions                 # start a chat (character + persona)
GET    /sessions/{id}            # history
POST   /sessions/{id}/messages   # send a turn -> returns SSE stream of tokens
POST   /sessions/{id}/regenerate # re-roll last response
GET    /sessions/{id}/memory     # inspect summary + retrieved context (debugging)
GET    /models                   # list models the backend exposes
PATCH  /settings                 # sampler params, backend URL, context size
```

**Generation params** (passed through to the backend): `temperature`, `top_p`, `top_k`, `repetition_penalty`, `max_tokens`, `stop`. Exposing these in the UI is what lets you dial in a character's "voice."

---

## 7. Phased Roadmap

> **Status:** Phases 0–4 are built and verified — see [`../README.md`](../README.md).
> That completes Route C below: rolling summary + keyword lorebook + vector RAG.

**Phase 0 — Skeleton (½ day) ✅ BUILT**
FastAPI app, config, `LLMClient` interface + Ollama adapter, `/models` and a raw `/chat` that streams from the model over SSE. Prove the streaming pipe end to end.

**Phase 1 — Cards + basic chat (2–3 days) ✅ BUILT**
V2 PNG importer, character storage, persona, session store in SQLite, prompt builder honoring V2 slot ordering, minimal web UI (character picker + chat + streaming). At this point you can hold a coherent in-character conversation.

**Phase 2 — Rolling summarization ✅ BUILT**
Token budgeting, threshold-triggered summarization, summary injection, memory-inspection endpoint. Conversations stay coherent past the context window.

Implemented via a **watermark** (`ChatSession.summarized_upto_id`) rather than reacting to the prompt builder's `dropped_messages`: that count fluctuates per turn with card size and sampler settings, whereas summarization needs a stable, monotonic boundary. Each fold rewrites the summary instead of appending, keeping it bounded. Failed folds leave the watermark untouched, so turns are retried rather than lost. Streaming now carries typed events so the UI can show compression rather than appearing to hang.

**Phase 3 — Keyword lorebook ✅ BUILT**
Parse `character_book`, keyword matching, budget-aware injection, plus a UI to view/edit lore entries. Authored world facts now surface on cue.

Full V2 semantics: `constant`, `selective` + `secondary_keys`, `case_sensitive`, `insertion_order`, `priority`, and `before_char`/`after_char` positioning, plus book-level `scan_depth`, `token_budget` and `recursive_scanning`.

Two decisions worth recording. **Matching is whole-word** rather than substring — a substring match fires an entry keyed `art` on "archive", and those false positives poison context invisibly. Keys with punctuation or non-Latin scripts fall back to substring, where boundaries don't apply. **Recursion is capped at 3 passes** regardless of the card's setting, since entries referencing each other would otherwise never terminate.

Book-level settings are stored as flat `lorebook_*` fields rather than nesting the book in an object, so cards imported before this phase still validate — pydantic fills the defaults.

**Phase 4 — Vector RAG ✅ BUILT**
Embedding model integration, vector store, retrieval, blended into the Memory Manager. Precise long-range recall.

Three decisions departed from the sketch above, each for a reason worth keeping.

**Neither sqlite-vec nor Chroma.** Vectors are float32 blobs in an ordinary table, scanned linearly. sqlite-vec needs `enable_load_extension`, which isn't compiled into every Python build, and a virtual table, which the additive-only migration helper cannot express; Chroma is a second service to run and back up. At single-user scale the scan isn't the bottleneck — ~65ms for 2000 × 768 in pure Python, against an embedding round-trip of the same order and a generation measured in seconds. Vectors are normalised at write time so scoring is a dot product, and a candidate cap bounds the worst case. `memory/rag.py` is the only module that would have to change if that ever stops being true.

**Retrieval searches only what the prompt isn't already carrying** — in practice, messages below the summarisation watermark. Re-injecting a turn that's about to appear verbatim in the history block spends budget to say the same thing twice, and duplicated text encourages models to repeat themselves. This makes retrieval and summarisation complements rather than alternatives: with summarisation off, nothing is condensed and retrieval correctly finds nothing.

**Messages are embedded after the turn completes, not at each insert.** One batched call covers both halves of the exchange, and the same code path backfills chats that predate the feature and repairs edited messages. Each vector stores its model and a hash of its source text, so a model switch or an edit marks vectors for recomputation instead of silently returning neighbours from a coordinate space that no longer applies.

Lorebook entries are *not* embedded. Semantic matching there would be additive to the V2 keyword contract rather than a replacement for it, and it's a different enough problem to belong in its own phase.

**Phase 5 — Polish & backends (ongoing)**
llama.cpp/vLLM adapters, sampler UI, regenerate/edit/branch, export/import of sessions, optional TTS/image hooks, optional multi-user.

---

## 8. Proposed File Structure

```
roleplay-server/
├── app/
│   ├── main.py                # FastAPI app + route registration
│   ├── config.py              # backend URL, model, context size, samplers
│   ├── llm/
│   │   ├── base.py            # LLMClient interface
│   │   ├── ollama.py          # Ollama adapter
│   │   └── openai_compat.py   # llama.cpp / vLLM / LM Studio adapter
│   ├── cards/
│   │   ├── v2_import.py       # PNG chunk extraction + JSON parse
│   │   └── models.py          # normalized card schema
│   ├── memory/
│   │   ├── manager.py         # orchestrates summary + lorebook + RAG
│   │   ├── summarizer.py      # Phase 2
│   │   ├── lorebook.py        # Phase 3
│   │   └── rag.py             # Phase 4
│   ├── prompt/builder.py      # V2 slot ordering + token budgeting
│   ├── chat/orchestrator.py   # per-turn flow
│   ├── db/                    # SQLAlchemy models + session store
│   └── routes/                # characters, sessions, personas, settings
├── web/                       # React (or HTMX) frontend
├── data/                      # SQLite db, imported card PNGs
└── pyproject.toml
```

---

## 9. Open Questions For You

1. **Frontend fidelity:** rich React UI (closer to SillyTavern) or minimal HTMX to move fast? (Affects Phase 1 scope.)
2. **Which model(s)** do you plan to run on your GPU? Model choice affects context-window budgeting and prompt formatting (e.g. chat template, instruct vs. base).
3. **Group chats / multiple characters per session** — needed, or strictly 1:1 for v1?
4. **Session branching** (re-roll and keep alternate timelines) — v1 or later?

Answer these and I'll turn this into a concrete Phase 0–1 scaffold you can run.
