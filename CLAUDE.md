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

**A summary that reads as roleplay is rejected, not stored.** Roleplay finetunes — the
models most likely to be pointed at this — sometimes answer "compress this scene" by writing
more of the scene. Seen live: a whole fold came back as stage directions and dialogue, then
got injected as "Story so far" on every later turn, pinning ~440 tokens of emotional dialogue
permanently in context. The vicious part is that it self-perpetuates: the next fold is handed
that summary and told to rewrite it, so dialogue begets dialogue. Measured against the real
model — a clean summary stayed clean whether the new turns were sparse or dense with
narration, while a degraded one reproduced itself. So the loop cannot start if a degraded
summary is never stored. Rejection also *is* the recovery path: the watermark stays put, the
same turns are refolded next time against a longer transcript, and a longer transcript is
what pulls the model back toward summarising.

**The retrieval query is one user turn by default, and never an assistant turn.** Two
separate dilution effects, both measured on real chats. Assistant replies
run several times longer than a user turn and are stylistically near-identical to one another
("*She nods...*"), so blending them in makes the query embed as generic narration and match
other narration. Measured live: "what was the innkeeper's name?" scored 0.81 against the
message naming him when queried alone, and fell out of the top four entirely once the
surrounding prose was mixed in — the model then invented a name. The user's turns carry
intent; the assistant's carry house style.

Excluding assistant turns was not sufficient, which is why `retrieval_query_messages`
defaults to **1**. The user's own adjacent turns dilute too: asking for the innkeeper while a
neighbouring turn said "look back at the woman in that temple" pulled two long opening-scene
passages above the answer, and they ate the token budget so it was never injected. Blending
even two questions was enough to break it. The fragment case this setting was meant to serve
("And your father's name?") retrieved correctly on its own anyway.

**Don't add nomic's `search_query:` / `search_document:` prefixes.** Tempting, since that is
what the model was trained with and our symmetric embedding looks like an oversight.
Measured: they raise the right answer 0.596 → 0.623 but raise the distractors more,
collapsing separation from +0.131 to +0.042. Tested and rejected, not overlooked.

**`num_ctx` is sent explicitly, and `context_tokens` is a lie without it.** Ollama does not
infer its window from the prompt — it applies its own default and silently truncates anything
longer *from the front*, which is exactly where the system prompt, the impersonation guard and
the character card sit. Demonstrated: an ~8500-token prompt with `num_ctx` unset answered
"the text provided does not mention a vault password" about a marker placed in its own first
line; with `num_ctx: 8192` the same prompt answered correctly. Raising `context_tokens`
without this does not widen the window, it just builds a longer prompt for the backend to
throw the top off. The summariser sends it too — a fold prompt carries a transcript slice and
can outgrow the chat prompt.

**A summary written in first or second person is rejected.** The third failure mode, and the
one that got past every earlier guard: a fold came back as the *user* speaking in character —
*"Elizabeth, remember that even when I am not physically present, my spirit will always be
with you…"* — fourteen first/second-person pronouns in seventy-nine words. No stage
directions, no quoted speech, no `Name:` label (the opening is a vocative comma), no "Riley
knew" construction. Fluent prose in the wrong voice, replacing a summary that had been
carrying real facts. Person is the cheap invariant the other checks were circling: whatever
else a summary is, it is written *about* these people rather than by one of them.

**A summary that narrates the *user's* interior life is rejected too.** Same shape as the
roleplay guard, same reason: it is re-injected every turn, so storing one teaches the model
that narrating the user is in bounds. Note what was tried first — adding a rule to the fold
prompt ("never write what {user} thought or felt"). Measured against the live model it did
nothing: from a contaminated log the model reproduces its opening near-verbatim, 4.25 such
phrases per summary before the rule and 5.50 after; from a clean log it produces none either
way. There is nothing for a rule to prevent and nothing it can repair. Don't re-add it.
The character's interiority is explicitly still allowed — a roleplay summary that couldn't say
how the character felt would be useless.

**`repeat_last_n` is set explicitly, because Ollama's default of 64 is a trap.** The repeat
penalty only looks back that many tokens. Replies here run ~300, so at the default the
penalty could not see the end of the sentence being written, let alone the previous turn —
phrasing recycled across the whole chat while `repeat_penalty` sat at a sensible-looking 1.1.
Measured by sampling continuations of a real prompt and scoring words not already used in
that chat: **15.4% new at 64, 24.4% at 1024**. Raising `repeat_penalty` to 1.18 on top
reached 29.7%, but that is the sharper instrument and it is already exposed; the window was
the part simply missing. Don't drop this option believing `repeat_penalty` alone covers it.

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

- **Model names must carry their namespace.** Bare names resolve to Ollama's official
  library; `ollama pull mythomax` fails with `pull model manifest: file does not exist`
  because MythoMax exists only under community namespaces. Found the hard way on a real
  Unraid deploy; a test now guards the unqualified name.
- **The default is a 7B, not the 13B this was designed around.** `HammerAI/smart-lemon-cookie`
  (Mistral 7B, Q4_K_M) needs ~4.9GB against MythoMax 13B's ~11GB, and the project advertises
  an 8GB card — the old default could not run on the hardware the docs promised. It's a merge
  of Alpaca-formatted models, so the prompt builder is unaffected. MythoMax remains the
  documented 12GB+ option, and `context_tokens: 4096` is still what the Alpaca builder
  assumes even though Mistral could go far higher.
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
- **`rag.probe()` and `rag.retrieve()` share `_score_all()` deliberately.** The probe is the
  calibration tool (Memory → Test recall); if it scored differently from the live path it
  would be worse than having no probe at all. Don't let them diverge. The probe returns
  rejects too — that's its entire reason to exist, since a threshold can't be chosen from
  data the floor already filtered out.

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

## Verification status

The project ran end to end on real hardware for the first time in August 2026. Keep this
section honest in both directions — don't repeat stale warnings, and don't inflate one
person's working setup into a general guarantee.

### Confirmed on real hardware

One deployment, one configuration: Unraid + RTX 2080 (8GB), `HammerAI/smart-lemon-cookie`
(Mistral 7B, Q4_K_M) for chat and `nomic-embed-text` for embeddings, at stock settings.

- **The image builds and runs.** GitHub Actions → GHCR → Unraid Compose Manager, GPU
  passthrough working. The Dockerfile, entrypoint and compose files are no longer
  statically-validated-only.
- **The Alpaca prompt works against a Mistral-based model.** Cards behave, stop sequences
  hold, the speaker-prefix stripping does its job.
- **Summarisation folds sensibly** and the summary reads as usable prose.
- **Retrieval was calibrated against `nomic-embed-text`, and the shipped floor was wrong.**
  Three positive probes (facts planted below the watermark) scored 0.65 / 0.72 / 0.84.
  Three negative probes — questions about events that never happened — still scored
  0.46 / 0.48 / 0.55. At the old `retrieval_min_score=0.45` **every** negative probe
  injected results; one filled all four slots with pure noise. The default is now **0.60**,
  which sits in the measured gap: the same six probes then produced four injections, all
  genuinely relevant, and rejected all three negatives.
  The lesson generalises — this embedding model scores unrelated text far higher than
  intuition suggests, so a floor that *feels* generous can be passing everything. Recalibrate
  with **Memory → Test recall** after any embedding-model change, and always include a
  negative probe; positives alone would have shown this configuration as working perfectly.

### Still unverified

- **`summary_trigger_tokens` does not control how much stays verbatim; `keep_recent_messages`
  does.** Raising the trigger from 1800 to 4000 cut fold *events* from 27 to 7 across an
  identical 34-turn run, but the end state was unchanged at 61 condensed / 8 verbatim — a
  fold always keeps exactly `keep_recent_messages`, so however rarely it fires, it catches up
  when it does. It was also actively worse: each fold then digested ~4000 tokens into a
  400-token budget, and one came back as a farewell speech in the user's voice. 1800 with an
  8192 window measured best. Raise `keep_recent_messages` if you want more verbatim history.
- **Long-chat behaviour was measured once, over 32 turns and 7 folds.** Repetition was not
  the problem people assume: consecutive replies overlapped 0.18 on vocabulary (max 0.37) and
  exactly one sentence recurred verbatim across 33 replies. Nor was card fixation — the story
  was walked deliberately from the card's temple through a road, a university archive and a
  forgery hearing, and card anchors fell to 0.25 per 100 words in the act that never mentioned
  them. The model follows the scene. What *did* break were the two memory bugs above, and a
  degraded summary looping in context is the most likely thing behind reports of "it keeps
  repeating itself".
- **Retrieval precision was measured on one chat, of nine folded messages.** Six probes is
  enough to show 0.45 was badly wrong; it is not enough to prove 0.60 is optimal. The margin
  either side is roughly 0.05, so a differently-worded question could still land the wrong
  side of it. Re-probe on a long real chat before treating 0.60 as settled.
- **Only `nomic-embed-text` has been characterised.** The floor is a property of the
  embedding model, not of this code — any other model needs its own calibration run.
- **MythoMax 13B has never actually run.** No longer the default, but it is still what the
  Alpaca prompt was designed against, and it remains the documented 12GB+ option. The only
  model this has been used with is the 7B, so prompt tuning reflects that one model.
- **Only one card has been exercised**, and it turned out to be misconfigured (its `name`
  was the *user's* role — see Gotchas). Card compatibility across a library is untested.
- **The Ollama `/api/embed` → `/api/embeddings` fallback** is still reasoned from API
  history, not observed against an older Ollama.
- **The XML templates** have not been imported into a live Docker tab; only the Compose
  Manager path has been walked.
