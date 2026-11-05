# Local AI Roleplay Server

Fully local roleplay chat: FastAPI backend + React UI, talking to a model on your own GPU.
Nothing leaves your machine.

**Installing on Unraid? → [`deploy/unraid/README.md`](deploy/unraid/README.md)**

Anywhere else with Docker + an NVIDIA GPU: `docker compose up -d`, then
<http://localhost:8000>.

Phases 0–4 of the roadmap in [`docs/architecture.md`](docs/architecture.md) are done (cards,
streaming, rolling summary, lorebook, vector recall), and on top of them: group chats, world
chats where people come and go, characters with their own thoughts, earned trust, a narrator,
texting, writing first, choices, voices and speech input. Tuned and measured on
L3-8B-Stheno-v3.2 on an 8 GB RTX 2080; the measurements behind each choice are in
[`CLAUDE.md`](CLAUDE.md).

---

## What works now

**Chats**
- **SillyTavern V2 card import** — drop in a community card PNG, get a playable character.
  Handles V1 flat cards, V2 (`spec: chara_card_v2`), and V3-shaped payloads. Or write one by
  hand, or have the model draft or expand a card from a one-line brief.
- **Group chats** — several characters in one scene, one speaking per reply; pick who answers
  or let the server choose.
- **World chats** — a chat based in a world rather than one character. You meet people one at a
  time, they speak first, and **Walk on** takes you somewhere else to meet someone new. What you
  tell one person stays with them; each keeps a short note of you that you can correct. Chapter
  headings mark each meeting.
- **Worlds** — a shared setting and lorebook for many characters, and generated locals
  (extras) who can visit or join a scene.
- **Texting mode** — the same chat as a phone thread, switching in and out.
- **Swipes** — regenerate keeps every version of the last reply to flip between.
- **Stop keeps what was written** — generation ends on the GPU and the partial reply is saved.
- **Choices** — three things you could do next, above the type box.
- **Read aloud and speak instead of typing** — Piper voices and Whisper, both on the server's
  CPU, nothing sent out.
- **Import and export a chat** — move one chat between installs, or keep a copy. Bundles load
  whole worlds and casts at once.

**Characters who feel alive**
- **Their own thoughts** — a private journal after each reply, and an opinion of you that
  shapes the next one.
- **Earned trust** — trust, affection and respect move with what you do, capped by the server.
  Romance is opt-in per card and never unlocks for a card written as a minor.
- **Writing first** — a character may message you on their own, within quiet hours, their own
  daily rhythm and a daily cap. Push notifications are optional and carry no content.
- **A narrator** — quiet lines about the world now and then, and a small event on request.
- **A scene tracker** — where, when, weather and who's present, updated after each reply and
  editable.
- **Living in the moment** — replies don't rush ahead, walk out or jump the clock.

**Under the hood**
- **Backend-agnostic inference** — Ollama or any OpenAI-compatible server (llama.cpp, vLLM,
  LM Studio, TGI).
- **Raw prompt builder in Alpaca, Llama 3 or ChatML format**, honoring V2 slot ordering and
  token budgeting learned per model. Format and samplers are saved per model.
- **Impersonation guard** — lines where the reply acts or speaks for you are removed before
  the reply is saved, so the habit can't snowball. A reply that slips into first person is
  redone once.
- **Rolling summary plus a fact list** — old turns fold into a memory log and a list of
  specifics that a rewrite can't silently drop. Pins keep a moment verbatim.
- **Keyword lorebook** and **vector recall** over condensed turns.
- **Test this model** — a scripted 12-turn check of any model from Settings.

**Everything from the UI** — personas, characters, worlds, models, settings, memory, scene,
journal and standings are all editable in the browser, on a phone too. A password and
two-factor protect it; the look is a warm dark "manuscript" made for long reading.

---

## Requirements

- Docker with Compose v2
- NVIDIA GPU. On Linux that means
  [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html);
  on Windows it means Docker Desktop with the **WSL2 backend** and a current NVIDIA driver on
  the host (don't install the toolkit inside WSL — the Windows driver provides it)
- ~10 GB disk for the model
- **~5 GB VRAM** for the default 7B. A 13B needs ~11 GB — see
  [Choosing a model](#choosing-a-model) below

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

The default is **`HammerAI/smart-lemon-cookie`** — a 7B that fits comfortably on an 8 GB
card, which is the hardware this project advertises. Budget VRAM by weights *plus* KV cache,
not by download size:

| | Weights (Q4_K_M) | KV cache @ 4096 | Total |
|---|---|---|---|
| `HammerAI/smart-lemon-cookie` (7B) — **default** | 4.4 GB | ~0.5 GB | **~4.9 GB** |
| `HammerAI/mythomax-l2` (13B) | 7.9 GB | ~3.2 GB | **~11.1 GB** |

The cache gap is wider than the parameter gap. Llama-2 13B uses full multi-head attention —
800 KB per token — while Mistral-7B uses grouped-query attention at 128 KB, six times
cheaper.

Smart-Lemon-Cookie is a merge of Silicon-Maid, Kunoichi and LemonadeRP, all **Alpaca**
formatted like MythoMax, so the prompt builder needs no changes. It also handles far longer
context, so on 8 GB you can raise `RP_CONTEXT_TOKENS` to 8192 and still fit.

**On 12 GB or more**, MythoMax 13B is the better writer and the model this project's prompt
was originally tuned against. Set it before first boot in `.env`:

```
RP_MODEL=HammerAI/mythomax-l2
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

Every adapter sends a fully-rendered raw prompt to the backend's *completion* endpoint rather
than `/v1/chat/completions` — that's the only way to keep exact control over card fidelity.
Ollama gets `raw: true` for the same reason, which also means **Ollama's own template is
never used**: the instruct format comes from **Settings → Prompt format**, and it must match
the model.

| Format | Models |
|---|---|
| Alpaca (default) | MythoMax L2, Smart-Lemon-Cookie and other Llama-2 / Mistral merges |
| Llama 3 | L3-8B-Stheno, Lumimaid and other Llama 3 tunes |
| ChatML | Qwen and many Mistral-Nemo tunes |

The wrong format still produces replies, which is what makes it easy to miss. Measured on
Stheno v3.2 (a Llama 3 tune) over three 16-turn chats, Alpaca against Llama 3: 18 of 48
replies ran into the length cap instead of ending, the narrator slid into first person, and
10 turns narrated the user, against 0, none and 3. Check the model card when you switch.

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
| `RP_RETRIEVAL_QUERY_MESSAGES` | 1 | Recent **user** turns forming the search query |

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
  prompt/               builder, instruct formats, token estimation
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

- **Token counts are estimates**, learned per model from the backend's own prompt counts
  after the first reply (until then ~3.6 chars/token).
- **Env vars are first-run defaults.** Once a setting is changed in the UI it's stored in the
  database and wins over the environment. Editing `.env` afterwards won't override it —
  change it in the UI, or delete the row from the `app_settings` table.
- **Editing a message below the memory watermark won't reach the model.** Its original text
  is already folded into the summary. The UI says so when it happens; edit the summary in the
  Memory panel instead.
- **Stop ends generation on the server too**, and keeps what was written as the reply.
- **Single-user, one password.** There are no accounts. Set a password under **Settings →
  Password** and every `/api` route refuses to answer without a session; until you do, the
  server is open to anything that can reach it and the settings panel says so in amber.
  A password is not a substitute for network controls — the container binds `0.0.0.0:8000`,
  so keep the published port on a trusted network or behind a VPN. Don't port-forward this
  to the internet.
- **No filesystem side effects at import.** Data directories are created in `init_db()` and
  on first avatar upload, not at module load — otherwise the image build and any tooling
  would break wherever `/data` isn't writable yet.
- **Small models make things up.** An 8B invents details about you, and in world chats about
  what someone did since you last met. The guard, the notes editor and the scene panel are how
  to correct it.
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

## Next

- **A lived-in world** — places you can edit, who's around by time of day, and generated locals
  waiting in each place.
- **World chat extras** — interruptions, gossip between people, companions who walk with you,
  and people who leave on their own.
- **Branching** — going back to an old turn and forking it.
- **Semantic lorebook matching** — embed `character_book` entries so paraphrases fire them,
  alongside the keyword contract rather than replacing it.

The memory tradeoffs behind all of this are laid out in
[`docs/architecture.md`](docs/architecture.md) §5.
