# Local AI Roleplay Server

Fully local roleplay chat: FastAPI backend + React UI, talking to a model on your own GPU.
Nothing leaves your machine.

**Installing on Unraid? → [`deploy/unraid/README.md`](deploy/unraid/README.md)**

Anywhere else with Docker + an NVIDIA GPU: `docker compose up -d`, then
<http://localhost:8000>.

**This is Phase 0–4** of the roadmap in [`docs/architecture.md`](docs/architecture.md) —
chat with SillyTavern V2 cards, personas, streaming, regenerate, rolling summarization, a
keyword-triggered lorebook, and vector recall over past turns. That completes the hybrid
memory design; Phase 5 is polish and extra backends.

---

## What works now

- **SillyTavern V2 card import** — drop in a community card PNG, get a playable character.
  Handles V1 flat cards, V2 (`spec: chara_card_v2`), and V3-shaped payloads.
- **Backend-agnostic inference** — Ollama or any OpenAI-compatible server (llama.cpp, vLLM,
  LM Studio, TGI). Swap via one env var.
- **Token streaming** over SSE, with stop/abort.
- **MythoMax-tuned Alpaca prompt builder** honoring V2 slot ordering, `{{char}}`/`{{user}}`
  substitution, and token budgeting that trims oldest-first.
- **Rolling summarization** — old turns fold into a running memory log so conversations stay
  coherent well past 4096 tokens. Inspectable and hand-editable.
- **Keyword lorebook** — authored world facts that inject only when their keywords appear.
  Full V2 semantics, editable in the UI.
- **Vector recall** — semantic search over turns the summary has already condensed, so the
  character can still name the innkeeper from chapter one. Off until you pull an embedding
  model; toggled from the Memory panel.
- **Everything managed from the UI** — no terminal needed after install:
  - **Personas** — create, edit and delete; swap which one you're playing mid-chat.
  - **Greeting picker** — choose among a card's alternate greetings when starting a chat.
  - **Character editor** — edit any card field, manage alternate greetings, view the
    lorebook, delete characters.
  - **Model manager** — list installed models, switch the active one, and download new ones
    with a progress bar.
  - **Message editing** — fix or delete any turn in the history.
  - **Chat renaming** and per-chat persona swapping.
- **Settings persist** — sampler and memory settings save to the database and survive
  restarts. Env vars are first-run defaults only.
- **Regenerate** — re-roll the last reply.
- **Prompt inspector** — see the exact text the model receives (Settings → Inspect built prompt).

---

## Requirements

- Docker with Compose v2
- NVIDIA GPU. On Linux that means
  [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html);
  on Windows it means Docker Desktop with the **WSL2 backend** and a current NVIDIA driver on
  the host (don't install the toolkit inside WSL — the Windows driver provides it)
- ~10 GB disk for the model
- **Enough VRAM for the model you pick** — the default is a 13B and needs ~11 GB.
  8 GB cards need a 7B instead; see [Choosing a model](#choosing-a-model) below

Verify GPU passthrough before anything else:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

If that prints your GPU you're set. If it errors, fix that first — everything below depends
on it, and a broken passthrough shows up later as "the model is mysteriously slow" (it's
running on CPU).

## Development

```powershell
git clone <your-repo>; cd roleplay-server

python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m pytest                      # 81 tests, ~6s

uvicorn app.main:app --reload --port 8000
cd web; npm install; npm run dev      # separate terminal, :5173
```

Running the frontend dev server separately needs CORS, which is off by default since the
container serves both from one origin. Set `RP_CORS_ORIGINS='["http://localhost:5173"]'`.

> **Windows note:** `.gitattributes` pins `docker-entrypoint.sh` to LF endings. Don't
> override it — with CRLF the container fails at startup with `bad interpreter: /bin/sh^M`.
> There's a test guarding this.

## Run

```bash
cp .env.example .env      # optional; defaults work as-is
docker compose up -d
```

That's the whole thing. Open <http://localhost:8000>.

First boot downloads MythoMax (several GB), so it takes a while. Watch it:

```bash
docker compose logs -f model-pull    # download progress
docker compose logs -f app           # server
```

## Choosing a model

The default, `HammerAI/mythomax-l2`, is a 13B and **needs about 11 GB of VRAM** — more than
its 7.9 GB download suggests, because the KV cache is charged on top:

| | Weights (Q4_K_M) | KV cache @ 4096 | Total |
|---|---|---|---|
| `HammerAI/mythomax-l2` (13B) | 7.9 GB | ~3.2 GB | **~11.1 GB** |
| `HammerAI/smart-lemon-cookie` (7B) | 4.4 GB | ~0.5 GB | **~4.9 GB** |

The cache gap is larger than the parameter gap. Llama-2 13B uses full multi-head attention —
800 KB per token — while Mistral-7B uses grouped-query attention at 128 KB per token, six
times cheaper.

**On 8 GB (2080, 3070, 4060):** use `HammerAI/smart-lemon-cookie`. It's a merge of
Silicon-Maid, Kunoichi and LemonadeRP, all Alpaca-formatted like MythoMax, so the prompt
builder needs no changes. It also handles far longer context, so you can raise
`RP_CONTEXT_TOKENS` to 8192 and still fit.

**On 12 GB or more:** the default 13B is the better writer.

Set it before first boot in `.env`:

```
RP_MODEL=HammerAI/smart-lemon-cookie
```

After first boot, `.env` no longer controls it — change the model in the **Models** panel
instead, since UI settings are stored in the database and take precedence.

If Ollama can't fit a model it silently splits it across GPU and CPU rather than failing, and
the CPU half becomes the bottleneck. Symptoms are low GPU utilisation and replies that crawl.
Check with:

```bash
docker exec roleplay-ollama ollama ps
```

`100% GPU` in the PROCESSOR column means it fits. Anything like `43%/57% CPU/GPU` means it
doesn't, and you want a smaller model.

## First run

1. Open <http://localhost:8000>. The status dot in the sidebar shows backend state — green
   is ready, amber means the selected model isn't downloaded, red means Ollama is
   unreachable. Click it to open the model manager.
2. **Import character card** → pick a V2 card PNG.
3. *(Optional)* **Personas** → create one, so the character knows who you are. Without one
   you're just "You".
4. Click the character in the sidebar. Pick your persona and which greeting opens the
   scene, then start.
5. Type a turn and hit Enter.

Hover any message to edit or delete it. Click the chat title to rename it.

## Everyday commands

```bash
docker compose up -d --build     # rebuild after code changes
docker compose down              # stop (data and models survive)
docker compose logs -f app       # tail the server
docker compose exec ollama ollama list    # what's downloaded

# Swap models
docker compose exec ollama ollama pull <tag>
# then set RP_MODEL=<tag> in .env and:
docker compose up -d
```

Chats and characters live in the `app-data` volume; models in `ollama-models`. Both survive
`down` and rebuilds. To wipe everything: `docker compose down -v`.

Back up your chats with:

```bash
docker run --rm -v roleplay_app-data:/data -v "$PWD:/backup" \
  alpine tar czf /backup/roleplay-backup.tar.gz -C /data .
```

## Architecture in the container

One image, one port. A multi-stage build compiles the React bundle in a Node stage, then
copies just the built assets into a Python runtime — Node and `node_modules` never reach the
final image. FastAPI serves that bundle at `/` and the API under `/api`.

The `/api` prefix isn't cosmetic: serving the UI and API from one origin means a static path
could otherwise shadow an endpoint. Prefixing removes the whole class of collision, and kills
CORS entirely since everything is same-origin.

```
┌──────────────── docker compose ─────────────────┐
│                                                 │
│  app  (roleplay-server:latest)   :8000 ─────────┼──► browser
│   ├── /       built React UI                    │
│   └── /api/*  FastAPI                           │
│         │                                       │
│         └──► ollama  :11434  ──► GPU            │
│                                                 │
│  volumes: app-data (chats) · ollama-models      │
└─────────────────────────────────────────────────┘
```

`model-pull` is a one-shot service that downloads the model once Ollama is healthy, then
exits. `app` waits on `service_completed_successfully`, so the server never starts against a
missing model.

> **Using llama.cpp or vLLM instead?** Drop the `ollama` and `model-pull` services, then set
> `RP_BACKEND=openai_compat` and `RP_LLM_BASE_URL` to its `/v1` endpoint. No code changes.

---

## Why the prompt looks the way it does

MythoMax L2 is a Llama-2 merge that expects **Alpaca** instruction formatting, not a chat
template. So every adapter sends a fully-rendered raw prompt to the backend's *completion*
endpoint rather than `/v1/chat/completions` — that's the only way to keep exact control over
card fidelity. Ollama gets `raw: true` for the same reason.

Slot ordering follows the V2 contract, which is what makes community cards behave as authored:

```
system_prompt (or default RP directive)
persona
description + personality + scenario
example dialogue          <- trimmed first under context pressure
[Phase 2] rolling summary
[Phase 3] triggered lorebook entries
[Phase 4] recalled past turns
chat history              <- trimmed oldest-first
### Instruction: post_history_instructions
### Response: {char}:
```

Two details worth knowing:

- **Stop sequences** are `### Instruction:`, `\n{user}:`, and `</s>`. The user label is
  newline-anchored on purpose — a bare `Riley:` would truncate legitimate prose like
  `she turned to Riley: "..."`.
- **Speaker-prefix stripping** happens on both the server (before persisting) and the client
  (mid-stream), so a model that echoes `Seraphine:` doesn't show the prefix and then have it
  vanish on reload.

---

## How memory works (Phase 2)

Once a chat outgrows the context window, older turns get **folded** into a running summary
that's injected near the top of the prompt as *"Story so far"*.

The mechanism is a **watermark** (`ChatSession.summarized_upto_id`). Messages at or below it
have been absorbed into the summary and are no longer sent verbatim; everything above it is
recent history. Each fold *rewrites* the whole summary rather than appending to it — that's
what keeps it bounded. An append-only log would grow until it ate the context window, which
defeats the entire purpose.

Deliberately, this is **not** driven by the prompt builder's `dropped_messages` count. That
number shifts every turn as card size and samplers change; summarization needs a stable,
monotonic boundary it can commit to.

Tuning (Settings panel, or `.env`):

| Setting | Default | Effect |
|---|---|---|
| `RP_SUMMARY_TRIGGER_TOKENS` | 1800 | Fold once un-summarized history exceeds this |
| `RP_KEEP_RECENT_MESSAGES` | 8 | Recent turns never folded — recency stays verbatim |
| `RP_SUMMARY_MAX_TOKENS` | 400 | Length cap on the memory log |
| `RP_SUMMARY_TEMPERATURE` | 0.3 | Low: recall task, not a creative one |
| `RP_SUMMARY_ENABLED` | true | Turn the whole thing off |

**The Memory panel** (header → Memory) shows how much is condensed vs. verbatim, progress
toward the next fold, and lets you edit the summary by hand or force a fold. Hand-editing
matters: summarization is lossy, and being able to correct a bad fold is the difference
between usable and infuriating.

Two safety properties, both tested:

- **A failed fold never advances the watermark.** If the model returns something unusable,
  the old summary is kept and the turns are retried next time — they're never dropped into a
  black hole.
- **A failed fold never breaks the chat.** The reply is already persisted before folding
  starts; errors surface as a `memory` event and the conversation carries on.

Folding blocks for a few seconds, so the stream emits typed `memory` events
(`compressing` → `done`/`failed`) and the UI shows a status chip instead of appearing hung.

---

## The lorebook (Phase 3)

Keyword-triggered world info. Where the summary carries *narrative flow*, the lorebook
carries *canon that must stay exact* — place names, factions, rules of magic — and costs
nothing until its keywords appear in recent messages.

Imported V2 cards bring their `character_book` with them. Edit it under a character's
**edit** link.

Per entry:

| Setting | Effect |
|---|---|
| **Keywords** | Any match fires the entry. Whole-word matching (see below) |
| **Content** | Injected verbatim when triggered |
| **Always inject** | Fires every turn regardless of keywords (`constant`) |
| **Require a second keyword** | Turns secondary keys into an AND condition (`selective`) |
| **Case sensitive** | Off by default |
| **Order** | Lower sorts earlier in the prompt (`insertion_order`) |
| **Priority** | When over budget, lowest is dropped first |
| **Position** | Before or after the character definition |

Per book: **scan depth** (how many recent messages are searched), **token budget** (cap on
injected lore), and **recursive** (entries can trigger other entries, capped at 3 passes so
a cycle can't hang).

**Matching is whole-word, deliberately.** A substring match would fire an entry keyed `art`
on the word "archive", or `he` on "the". That kind of false positive quietly poisons context
and is miserable to debug. Keys containing punctuation or non-Latin scripts fall back to
substring matching, since word boundaries are meaningless there.

To see what actually fired, open **Settings → Inspect built prompt** — triggered entries are
listed as chips, along with how many were dropped for budget.

---

## Vector recall (Phase 4)

Summarization is good at narrative flow and bad at specifics — by its second fold, "the
innkeeper introduced himself as Bram Halloway" has usually become "they spoke with the
innkeeper". Vector recall is the fix: every message is embedded, and each turn the most
relevant *condensed* turns are pulled back into the prompt verbatim.

It's the third memory source, not a replacement. The summary carries the plot, the lorebook
carries authored canon, recall carries exact details.

**Setup.** It's off by default because it needs a second model:

```bash
docker compose exec ollama ollama pull nomic-embed-text
```

or from the UI: **Models → Download → `nomic-embed-text`**, then **Memory → Vector recall**.
Switching it on embeds the existing backlog for that chat immediately.

**What gets searched.** Only messages *below the summarization watermark* — turns the prompt
is no longer carrying verbatim. Retrieving a message that's about to appear in the history
block anyway spends budget to say the same thing twice, and duplicated text nudges models
into repeating themselves. A consequence worth knowing: with `RP_SUMMARY_ENABLED=false`
nothing is ever condensed, so recall has nothing to search. The two features are designed to
work together.

Tuning (Settings panel, or `.env`):

| Setting | Default | Effect |
|---|---|---|
| `RP_RETRIEVAL_ENABLED` | false | The switch |
| `RP_EMBEDDING_MODEL` | nomic-embed-text | Any embedding model your backend serves |
| `RP_EMBEDDING_BASE_URL` | *(blank)* | Blank = same host as the chat model |
| `RP_RETRIEVAL_TOP_K` | 4 | Most past turns re-injected per reply |
| `RP_RETRIEVAL_MIN_SCORE` | 0.60 | Cosine floor — below this a hit is noise |
| `RP_RETRIEVAL_BUDGET_TOKENS` | 400 | Context spent on recall |
| `RP_RETRIEVAL_QUERY_MESSAGES` | 3 | Recent turns forming the search query |

### Calibrating the relevance floor

RAG's failure mode differs in kind from the others: a misfire produces context that is
plausible but irrelevant, which is far harder to notice than a missing fact — the character
just raises an unrelated old scene as though it mattered.

Use **Memory → Test recall**. Type any question and it scores it against the chat's memory
without generating anything, listing every candidate — including the ones the floor rejects,
annotated with why. The prompt inspector can't do this: it only shows what already passed,
and you can't choose a threshold from data that was filtered out before you saw it.

What to look for is **separation**, not an absolute number:

1. Ask about something you know happened. Note what the right message scores.
2. Ask about something that **never happened**. Note what the best result scores.
3. Put `RP_RETRIEVAL_MIN_SCORE` in the gap.

Step 2 is the one people skip and the one that matters — here's what it caught on a real
chat with `nomic-embed-text`:

| Probe | Best score |
|---|---|
| "What was the innkeeper's name?" | 0.72 ✅ the right message |
| "Tell me about my sister who died." | 0.65 ✅ the right message |
| "Which key opens the reliquary?" | 0.84 ✅ the right message |
| "What did the dragon say?" *(never happened)* | 0.55 ❌ noise |
| "How much for the horse?" *(never happened)* | 0.48 ❌ noise |
| "What colour was the airship?" *(never happened)* | 0.46 ❌ noise |

The original 0.45 default passed **all** of it — every invented question injected results,
one filling all four slots with unrelated narration. Positive probes alone would have shown
this as working perfectly. At 0.60 the same six probes inject four results, all correct, and
reject every negative.

That's why the default is 0.60, and why the floor is a property of your **embedding model**
rather than of this app. If the two ranges overlap, no threshold will work — raise
`RP_RETRIEVAL_QUERY_MESSAGES` to give the query more context, or try a different embedding
model.

A free signal during normal play: if the inspector returns a full `RP_RETRIEVAL_TOP_K` on
nearly every turn, including turns where nothing old is relevant, the floor is too low.

Two safety properties, both tested:

- **A dead embedding backend never costs you a turn.** Indexing and retrieval are best-effort
  around generation; failures surface as a `memory` event and the reply proceeds without
  recall.
- **Stale vectors are never compared.** Each vector records the model and a hash of the text
  it came from. Editing a message or changing the embedding model marks the affected vectors
  for recomputation rather than silently returning wrong neighbours.

Embeddings live in the same SQLite file as everything else, as float32 blobs scanned
linearly — no vector-store extension, no extra service. At single-user scale that's ~65ms for
2000 vectors, against an embedding round-trip of a similar order and generation that takes
seconds. `RP_RETRIEVAL_MAX_CANDIDATES` bounds the worst case.

---

## Layout

```
CLAUDE.md               context for Claude Code — read before changing anything
Dockerfile              multi-stage: node build -> python runtime
docker-entrypoint.sh    PUID/PGID alignment, then drops privileges
docker-compose.yml      app + ollama + one-shot model pull
tests/                  pytest suite (104 tests)
deploy/unraid/          Unraid compose, Docker templates, install guide
docs/architecture.md    design decisions + phase roadmap
.github/workflows/      publishes the image to GHCR
app/
  config.py             env-driven settings
  llm/                  LLMClient interface + Ollama / OpenAI-compat adapters
    embeddings.py       Embedder interface + the same two backends
  cards/                V2 PNG import + normalized schema
  prompt/               Alpaca builder + token estimation
  memory/
    manager.py          watermark logic, fold triggering, lore assembly
    summarizer.py       fold prompt + LLM call
    lorebook.py         keyword matching, budget eviction
    rag.py              message embedding, cosine search, budget eviction
  chat/orchestrator.py  per-turn flow, typed stream events
  db/                   SQLAlchemy models (SQLite) + additive migrations
  routes/               characters, personas, sessions, system
web/src/                React UI
```

Container layout:

```
/app/app      application code
/app/static   built React bundle (from the web stage)
/data         volume: SQLite db + character avatars
```

Runs as a non-root user (uid 10001). `/data` is created and chowned in the image so the
named volume inherits writable ownership on first mount.

## API

Everything is under `/api`. Interactive docs at <http://localhost:8000/api/docs>.

```
GET   /api/health                     backend reachability + available models
GET   /api/characters                 list; POST /api/characters/import to add
GET   /api/sessions                   list; POST to create
POST  /api/sessions/{id}/messages     send a turn → SSE token stream
POST  /api/sessions/{id}/regenerate   re-roll the last reply

GET   /api/sessions/{id}/memory       summary, watermark, counts, index coverage
PATCH /api/sessions/{id}/memory       hand-edit the summary
POST  /api/sessions/{id}/summarize    force a fold, ignoring the threshold
POST  /api/sessions/{id}/reindex      embed anything without a current vector
POST  /api/sessions/{id}/retrieve     score a query against memory, no generation
GET   /api/sessions/{id}/prompt       exact prompt text, incl. what was recalled
```

---

## Notes & known limits

- **Token counts are estimates** (~3.6 chars/token, deliberately conservative). Swap in a real
  tokenizer in `app/prompt/tokens.py` if you want exact budgeting.
- **Env vars are first-run defaults.** Once a setting is changed in the UI it's stored in the
  database and wins over the environment. Editing `.env` afterwards won't override it —
  change it in the UI, or delete the row from the `app_settings` table.
- **Editing a message below the memory watermark won't reach the model.** Its original text
  is already folded into the summary. The UI says so when it happens; edit the summary in the
  Memory panel instead.
- **Stop during streaming aborts the client, not the server.** The backend finishes its turn
  and persists it; the UI resyncs. Cancelling server-side generation is a Phase 5 item.
- **Single-user, no auth.** The container binds `0.0.0.0:8000`, so anything that can reach
  the host can use it. Keep the published port on a trusted network, or put a reverse proxy
  with auth in front. Don't port-forward this to the internet.
- **No filesystem side effects at import.** Data directories are created in `init_db()` and
  on first avatar upload, not at module load — otherwise the image build and any tooling
  would break wherever `/data` isn't writable yet.
- **1:1 chats only** and simple regenerate (no branching/swipes) — both deliberate v1 scope.
- **Summarization is lossy.** It preserves narrative flow well and precise facts poorly —
  that's inherent to the approach, not a bug. Vector recall is the fix for exact recall, and
  the Memory panel lets you correct anything important the fold dropped.
- **Vector recall only searches condensed turns.** With summarization off, nothing is ever
  condensed and recall finds nothing. The two are complements, not alternatives.
- **Retrieval defaults work but aren't proven precise.** They've returned relevant results
  against `nomic-embed-text` on one real setup, unchanged. What hasn't been tested is the
  opposite direction: whether `RP_RETRIEVAL_MIN_SCORE` actually *rejects* irrelevant matches.
  That's the failure mode worth watching, because recall that's plausible but wrong reads as
  fine. If the prompt inspector returns a full `RP_RETRIEVAL_TOP_K` on nearly every turn, the
  floor is likely too low — raise it until unrelated turns stop appearing.
- **Regenerate doesn't rewind the watermark.** If a fold already absorbed earlier turns,
  re-rolling won't un-condense them.
- **Schema changes** are applied by a small additive migration in `app/db/database.py`
  (SQLite only). Existing Phase 1 databases upgrade in place on startup.

---

## Next: Phase 5

Polish and reach, now that the hybrid memory design is complete:

- **Cancelling generation server-side** — today Stop aborts the client only.
- **Branching and swipes** — keep alternate timelines instead of destructive regenerate.
- **Session export/import** — move a chat between installs.
- **Semantic lorebook matching** — embed `character_book` entries so paraphrases fire them,
  alongside the keyword contract rather than replacing it. The V2 spec is keyword-based, so
  this has to be additive.
- **Optional TTS / image hooks**, and multi-user if it's ever wanted.

The memory tradeoffs behind all of this are laid out in
[`docs/architecture.md`](docs/architecture.md) §5.
