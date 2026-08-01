# CLAUDE.md

Context for Claude Code working on this repo. Read this before changing anything.

## What this is

A fully local AI roleplay chat server. FastAPI backend + React UI, talking to a model on the
user's own GPU. No cloud calls, ever — that's the whole point of the project, so never
introduce a dependency that phones out at runtime.

Deployment target is **Unraid** (Docker Compose or native Docker templates). Development
happens on **Windows**.

**Status: Phases 0–4 complete and tested. Phase 5 (polish + extra backends) is next.**

## Commands

```powershell
# Tests — 104 of them, ~8 seconds. Run these before and after any change.
python -m pytest
python -m pytest tests/test_lorebook.py -v    # one file
python -m pytest -k watermark                 # one concern

# Backend, without Docker
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000

# Frontend
cd web; npm install; npm run build      # build (what the Docker image does)
cd web; npm run dev                     # dev server on :5173

# Full stack
docker compose up -d --build
```

Requires Docker Desktop with the **WSL2 backend** for GPU passthrough. Verify with
`docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi` before debugging
anything GPU-related.

## Architecture

```
app/
  config.py             pydantic-settings; env vars are FIRST-RUN DEFAULTS ONLY
  settings_store.py     DB-backed runtime settings; overrides env at startup
  llm/
    base.py             LLMClient interface — the only thing above this layer
    ollama.py           /api/generate with raw=true, plus model pull
    openai_compat.py    /v1/completions for llama.cpp / vLLM / LM Studio
    embeddings.py       Embedder interface + both backends (separate from LLMClient)
    factory.py          explicit singletons + reset_client()/reset_embedder()
  cards/                V2 PNG import, normalized schema
  prompt/
    builder.py          Alpaca assembly, slot ordering, token budgeting
    tokens.py           chars/3.6 estimate — no tokenizer dependency
  memory/
    manager.py          watermark logic; composes summary + lorebook + retrieval
    summarizer.py       fold prompt + LLM call
    lorebook.py         keyword matching, budget eviction
    rag.py              message embedding, cosine search, budget eviction
  chat/orchestrator.py  per-turn flow, typed stream events
  db/                   SQLAlchemy models + additive migrations
  routes/               characters, personas, sessions, system
web/src/components/     React UI
tests/                  pytest suite
deploy/unraid/          compose, Docker templates, install guide
```

## Decisions that look wrong but aren't

Each of these was deliberate. Changing them will reintroduce a bug that was already fixed.

**Raw completion endpoints, not `/v1/chat/completions`.** MythoMax L2 is a Llama-2 merge
expecting Alpaca instruction format, not a chat template. We render the full prompt ourselves
and send it raw (`raw: true` on Ollama). This is what makes SillyTavern cards behave as
authored. Switching to chat-completions would silently degrade every card.

**The API is namespaced under `/api`.** The UI and API share one origin in the container. An
unprefixed API means a static asset path could shadow an endpoint. Also kills CORS entirely.

**No filesystem side effects at import time.** Directories are created in `init_db()` and on
first avatar upload, never at module load. A previous version ran `os.makedirs('/data')` at
import, which made the module unimportable anywhere `/data` wasn't writable — it broke the
Docker build and every test.

**`factory.get_client()` uses an explicit singleton, not `lru_cache`.** `reset_client()` must
close whatever is currently live. With `lru_cache` it had to *construct* a client in order to
close it, so saving settings failed whenever the current config was unbuildable.

**Lorebook matching is whole-word, with a CJK carve-out.** Substring matching fires an entry
keyed `art` on "archive" and `he` on "the" — false positives that poison context invisibly.
But Python's `\w` matches CJK, so boundary matching can *never* fire for 書庫 inside Japanese
text (neighbouring kana are word characters). Scripts without spaces are explicitly excluded
from boundary matching in `lorebook.py`. Both directions are tested.

**Summarization uses a watermark, not the prompt builder's `dropped_messages`.** That count
fluctuates every turn with card size and sampler settings. Folding needs a stable, monotonic
boundary it can commit to.

**A fold rewrites the summary rather than appending.** That's what keeps it bounded. An
append-only memory log grows until it eats the context window, defeating the purpose.

**A failed fold must not advance the watermark.** Otherwise turns vanish into a black hole.
`summarize_now()` only advances if the summary actually changed. Tested.

**Stop sequences are newline-anchored (`\nRiley:`, not `Riley:`).** A bare name would
truncate legitimate prose like `she turned to Riley: "..."`.

**`post_history_instructions` is wrapped in `### Instruction:`.** A bare paragraph between
two `### Response:` headers confuses Llama-2 instruct tunes.

**Lorebook book-level settings are flat `lorebook_*` fields.** Nesting them in an object
would break every card already stored in a user's database.

**Embeddings are float32 blobs scanned linearly, not sqlite-vec.** An extension means
`enable_load_extension`, which isn't compiled into every Python build, and a virtual table,
which the additive-only migration helper can't express. The scan is ~65ms for 2000×768 in
pure Python — same order as the embedding round-trip it accompanies, and trivial next to
generation. Vectors are normalised at write time so scoring is a dot product, and
`retrieval_max_candidates` bounds the worst case. Benchmark before assuming this needs numpy.

**Retrieval only searches messages below the watermark.** Anything still pending is about to
be sent verbatim, so retrieving it buys a duplicate — and duplicated text measurably pushes
models toward repeating themselves. The consequence is deliberate: with `summary_enabled`
off, nothing is ever folded and retrieval correctly returns nothing. The two features are
complements. Don't "fix" this by widening the candidate set to all messages.

**Indexing runs after the reply is persisted, not before generation.** One batched embed call
then covers both halves of the turn and leaves nothing unindexed. Moving it earlier was tried:
it left the newest assistant message permanently stale in the coverage report, and gained
nothing, because retrieval embeds the query text directly rather than looking up the current
message's vector.

**`Embedder` is a separate interface from `LLMClient`, not a method on it.** The embedding
model is a *different model* from the chat model — `nomic-embed-text` runs alongside MythoMax,
not instead of it — and may live on another host. Folding `embed()` into `LLMClient` would
force every adapter to carry a second model name it mostly can't use.

**Each vector stores its model and a hash of its source text.** A vector from a different
embedding model shares no coordinate space with the current one, and a vector of since-edited
text describes something that no longer exists. Both are recomputed rather than compared;
that's what makes the index self-healing across edits and model switches.

**`.gitattributes` forces LF on `*.sh`.** On Windows, Git would check out
`docker-entrypoint.sh` with CRLF and the container dies with `bad interpreter: /bin/sh^M`.
There's a test asserting the file has no CRLF.

## Gotchas

- **There is no bare `mythomax` in Ollama's official library.** `ollama pull mythomax` fails
  with `pull model manifest: file does not exist`. MythoMax exists only under community
  namespaces; the default is `HammerAI/mythomax-l2` (Gryphe/MythoMax-L2-13b, Q4_K_M, 4K
  context — which is what `context_tokens: 4096` and the Alpaca builder assume). This was
  found the hard way on a real Unraid deploy; a test now guards the unqualified name.
- **`model-pull` always exits 0.** The app gates on that container *completing successfully*,
  and the Models panel is the only place to fix a bad model name — so a hard failure there
  locked the user out of the very UI that repairs it. Failures now warn loudly and let the
  stack come up. Don't "tidy" this back into an `&&` chain.
- **Env vars are first-run defaults only.** Once a setting is written from the UI it lives in
  the `app_settings` table and wins. Editing `.env` afterwards does nothing. Delete the row
  to revert.
- **Editing a message below the watermark won't reach the model** — its text is already
  folded into the summary. The API returns `below_watermark: true` and a note; the UI shows
  it in amber.
- **Unraid's default bridge network has no inter-container DNS.** With the XML templates,
  `http://ollama:11434` will not resolve — the app must point at the host LAN IP. Compose
  users are fine (project networks do resolve). This is the single most common install error.
- **`MemoryContext.lore` is a derived property**, concatenating `lore_before` + `lore_after`.
  Set the two position lists, not `lore`.
- **The DB migration helper is additive-only** (`_ADDED_COLUMNS` in `db/database.py`). It
  adds columns to existing SQLite tables since `create_all` won't. If you need to drop or
  rename, that needs a real migration story — probably Alembic. `message_embeddings` needed
  no entry: it's a new *table*, which `create_all` does handle.
- **`build_context()` and `build_turn_prompt()` are async** — retrieval embeds the query, which
  is a network call. `build_turn_prompt` returns `(card, ctx, built)`; the extra `ctx` exists
  so `/prompt` can report on retrieval without building the context a second time and paying
  for a second embed.
- **Retrieval is off by default** (`retrieval_enabled=False`). It needs a second model pulled,
  and enabling it before that exists adds a failing HTTP round-trip to every turn.
- **The `/prompt` inspector's retrieval query is the last message**, which after a completed
  turn is the *assistant's* reply, not the user's question. That's correct for "what would the
  next prompt look like", but it makes the inspector a poor oracle in tests for "what was
  recalled for my question" — assert against `MockLLM.prompts` instead.
- **Retrieval and indexing never raise into the turn.** A dead embedding backend surfaces as a
  `memory` stream event and `RetrievalResult.error`; the reply proceeds without recall. Keep it
  that way — memory is best-effort around generation, not a precondition for it.

## Conventions

- Comments explain **why**, not what. Assume the reader can read Python.
- New settings: add to `config.py`, and to `settings_store.PERSISTABLE` if the UI should
  change it at runtime. Add to `CLIENT_KEYS` if it affects LLM client construction.
- New API routes go under `/api` via the routers in `app/routes/`.
- The UI is the source of truth. **Anything a user might need should be doable from the web
  interface, not a terminal.** This was an explicit requirement — don't add features that
  require `docker exec` or hand-editing files.
- Frontend uses shared primitives in `web/src/components/ui.jsx` (`Modal`, `Field`, `Input`,
  `Textarea`, `Button`, `Empty`). Use them rather than restyling from scratch.
- Tailwind palette: `ink-950/900/850/800/700` surfaces, `accent` (#a78bfa) for interactive.

## Testing notes

`tests/conftest.py` sets env vars **before** importing `app.config` — that ordering matters.
It also lowers the fold threshold to 300 tokens so memory tests trigger in a few turns.

`MockLLM` recognises the summarisation prompt by the string "memory log" and returns a
`FOLD#n` marker, which is how memory tests assert folding happened.

The `_reset_state` fixture drops and recreates tables and resets the settings singleton
between tests — settings are process-wide and the settings API deliberately mutates them.

**`test_settings_survive_a_real_restart` spawns a subprocess on purpose.** Reloading modules
in-process creates a second settings object and proves nothing — an earlier version of this
test passed while the feature was broken.

`MockEmbedder` returns bag-of-words vectors over an md5-hashed vocabulary, not noise, so
texts sharing words genuinely score higher. That's what lets `test_retrieval.py` assert the
*right* message came back rather than merely that something did. md5 rather than `hash()`
because Python randomises string hashing per process.

The `enable_retrieval()` helper pins `retrieval_min_score=0.35` deliberately: shared stopwords
alone score ~0.24 against `MockEmbedder` and a real topical match ~0.46, so the threshold sits
between them with margin on both sides. `test_recalls_a_fact_that_was_summarised_away` and
`test_unrelated_history_does_not_surface` are the same setup at that same threshold with
opposite questions — together they show the floor is separating signal from incidental
overlap. Don't loosen it to make one of them pass.

## Phase 5 — polish & backends (next)

No single big rock left; the hybrid memory design of `docs/architecture.md` §5 Route C is
complete. Candidates, roughly by value:

- **Cancel generation server-side.** Stop currently aborts the client only; the backend
  finishes and persists the turn. Needs a per-session cancellation handle the route can
  signal.
- **Branching / swipes.** Regenerate is destructive today. Alternate timelines mean a parent
  pointer on `Message` and a UI for walking siblings — the schema change is the real work.
- **Session export/import** as a JSON bundle, so a chat can move between installs.
- **Semantic lorebook matching.** Embedding `character_book` entries would catch paraphrases
  keywords miss. It must be *additive* to keyword matching, not a replacement — the V2 spec
  is keyword-based and imported cards depend on that behaviour. `rag.py` already has the
  embedding plumbing; the work is a second candidate source in `lorebook.select` and a
  policy for combining the two signals.
- **Real tokenizer** in `prompt/tokens.py` if budgeting ever needs to be exact.

Whatever comes next, keep using mock LLM/embedder doubles so the suite stays fast and offline.

## What has never been verified

Be honest about this with the user rather than implying otherwise:

- **The Docker image has never been built.** It was developed in a sandbox with no Docker
  daemon. Compose files, Dockerfile and entrypoint are statically validated by
  `tests/test_packaging.py`, and the `pip install .` + `npm run build` steps were each run
  directly — but `docker compose up` has never executed.
- **The stack has never run against a real model.** Every test uses `MockLLM`. Prompt format,
  stop sequences and summarization quality are reasoned from the MythoMax/Alpaca spec, not
  observed. Expect to tune `DEFAULT_SYSTEM` and the summarizer prompt once you see real
  output.
- **Retrieval has never run against a real embedding model.** `MockEmbedder` is bag-of-words,
  so it validates the *plumbing and policy* — thresholds, budgets, dedupe, failure handling —
  but says nothing about what `nomic-embed-text` actually scores. `retrieval_min_score=0.45`
  is a guess. Calibrate it against real output via Settings → Inspect built prompt, which
  lists each hit with its score, before trusting the default.
- **The Ollama `/api/embed` → `/api/embeddings` fallback is untested against a real old
  Ollama.** The 404-latching path in `OllamaEmbedder` is reasoned from the API history, not
  observed.
- **No Unraid install has completed.** One was attempted: the compose stack reached
  `model-pull`, which failed on the bad `mythomax` tag (see Gotchas) and blocked the app from
  starting. Both causes are fixed, but the run has not been repeated — so everything past
  `model-pull` remains unverified, including the image build itself.
