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
# Tests — ~200 of them, ~10 seconds. Run these before and after any change.
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
  profiles.py           per-model format + samplers: saved profiles, built-in presets
  eval.py               "Test this model": scripted chat through stream_reply, scored
  settings_store.py     DB-backed runtime settings; overrides env at startup
  llm/
    base.py             LLMClient interface — the only thing above this layer
    ollama.py           /api/generate with raw=true, plus model pull
    openai_compat.py    /v1/completions for llama.cpp / vLLM / LM Studio
    embeddings.py       Embedder interface + both backends (separate from LLMClient)
    factory.py          explicit singletons + reset_client()/reset_embedder()
  cards/                V2 PNG import, normalized schema; creator.py drafts cards with the model
  prompt/
    builder.py          slot ordering, token budgeting, guard + style directives
    formats.py          Alpaca / Llama 3 / ChatML wrappers (selected by prompt_format)
    tokens.py           chars/token estimate, learned per model from Ollama's prompt counts
  memory/
    manager.py          watermark logic; composes summary + lorebook + retrieval
    summarizer.py       fold prompt + LLM call
    lorebook.py         keyword matching, budget eviction
    scene.py            location / time / weather / present, updated after each reply
    rag.py              message embedding, cosine search, budget eviction
  chat/orchestrator.py  per-turn flow, typed stream events
  chat/guard.py         strips narration of the user's actions before a reply is stored
  chat/group.py         group chats: cast, speaker choice, per-speaker history roles
  db/                   SQLAlchemy models + additive migrations
  routes/               characters, personas, sessions, system, worlds
web/src/components/     React UI
tests/                  pytest suite
deploy/unraid/          compose, Docker templates, install guide
```

## Decisions that look wrong but aren't

Each of these was deliberate. Changing them will reintroduce a bug that was already fixed.

**Raw completion endpoints, not `/v1/chat/completions`.** We render the full prompt ourselves
and send it raw (`raw: true` on Ollama). This is what makes SillyTavern cards behave as
authored. Switching to chat-completions would silently degrade every card. Raw also means
Ollama's Modelfile template is never applied -- the format is ours to render.

**The instruct format is an explicit setting, not detected.** `prompt_format` picks Alpaca,
Llama 3 or ChatML in `prompt/formats.py`; the slot ordering is shared. Detection from
Ollama's template was rejected because that template can be wrong: L3-8B-Stheno-v3.2, a
Llama 3 tune, arrived from Hugging Face with a ChatML template. Measured on Stheno over
3x16 turns, Alpaca vs Llama 3: replies hitting the length cap 18 vs 0, user-narrating turns
10 vs 3, and Alpaca's narrator drifted into first person. Formats never write BOS -- Ollama
adds one even with `raw: true` ("hello" costs 2 prompt tokens, "<|begin_of_text|>hello" 3).
Alpaca output is byte-identical to before; tests pin that.

**Format and samplers are saved per model (`app/profiles.py`, table `model_profiles`).**
Switching models used to keep the previous model's format and samplers, which is how
Stheno ran on Alpaca without anything looking broken. A switch now saves the old model's
profile first (before the patch applies, so a combined patch can't leak into it), then
restores the new model's saved profile, else a name-matched preset, else keeps the current
values and the Models panel says so. Keys sent in the same patch beat the profile. Presets
set only what is known about a model; the Stheno one is the measured setup.

**A shared world joins the card's lorebook; it doesn't replace it.** `World` holds a
description and entries in the card schema; `Character.world_id` links a character to one.
`lorebook.select` pools the world's entries with the card's under the *card's* scan depth and
budget, so membership costs no more context than the same entries in the card. The world
description goes in the head as "Setting:", above the character block it frames. Deleting a
world keeps its characters (they leave it). Membership is set by its own endpoint, not the
card PATCH, since it is not card text.

**A group chat is one speaker per reply, each from its own side (`chat/group.py`).** A
session keeps its `character` (greeting, world, the side summaries fold from) and adds
`SessionMember` rows; `Message.speaker_id` records who wrote a reply (None = the session's
character, so old chats read unchanged). Every prompt is still two-sided: the speaker's card
and lore, its lines as assistant turns, and the other characters' lines as *user-side* turns
labelled by name (`other:Name` history roles). On the assistant side they would hand the
speaker a transcript where it already wrote as someone else -- the narrate-the-user feedback
loop again. The others get a one-line "Also in this scene" plus a short blurb, a
`\nName:` stop each, and the guard runs for each of their names too. Who answers: the
user's pick, else the one character the message names, else whoever waited longest. A swipe
is re-rolled by whoever wrote it. Folds, facts, pins and recall label each line by speaker.
Measured offline on Stheno (Dina card, 6 seeds each, raw replies scored for narrating or
voicing the user or slipping into first person): one-on-one baseline 4/6, group answering
the user 3/6, group speaking straight after another character 3/6 -- the group layout adds
nothing over the solo rate the guard and redo already handle. Alternatives were no better:
the other's line as a system turn 3/6 and 6/6, a "Tommy has just spoken" tail cue 2/6 and
5/6, merged into the user's turn 4/6 and 4/6, and on the assistant side the speaker simply
carried on as Tommy. Live on the test box, 9 turns of Dina + Tommy: every speaker pick right
(named, rotation, pick, respond, swipe), two first-person redos, guard trims on five
replies, no stored line narrating the other character. Known limits: one line of the user's
speech survived as an unattributed quote in its own paragraph (the guard's existing gap),
and the scene's "present" can drop a member the exchange doesn't mention.

**The character creator drafts labelled sections, never saves, and is judged per section
(`cards/creator.py`).** An RP tune asked for JSON writes prose in braces; a form prefilled
with "Name:" it fills like the scene tracker. Each section is checked alone (a one-line
name, a description of 200+ chars, a greeting of 80+), so one bad section costs that
section. The draft returns to the editor with tick-boxes; expanding a card pre-ticks only
fields that are empty or that the draft lengthens, so a hand-written greeting isn't swapped
out unnoticed. Prototyped on Stheno (8-12 s a card): from a one-line brief, a world, and
Laura's two-sentence card. Fixes from those runs, each pinned by a test: with a world's cast
listed, the greeting addressed Tommy as the user -- the prompt now says the user is none of
them; expanding skipped the example dialogue -- now asked for explicitly; the examples
sometimes follow the greeting unlabelled from `<START>`, and a greeting can lose its opening
asterisk -- both repaired in parsing. Names that clash with an existing card are flagged,
not refused.

**Scene state is updated after every reply and read back in the tail.** A short extra
generation (`memory/scene.py`, ~0.6 s on the 2080) fills four fields, prefilled with
"Location:"; a reply that isn't all four short, quote-free fields keeps the old scene. It goes
in the tail because where the story *is* is what small models lose first. A swipe starts from
`scene_prev`, the scene before the reply it replaces. Prototyped on a 14-turn live transcript:
"change a field only when the exchange changes it" made the model stick (it stayed "inside the
stable, late morning" through a snowy patrol and "back home that evening"); asking where the
exchange *ends*, naming moves and time skips, followed every change. Then, verified live, the
model anchored on the old scene when it came first: "back home that evening" stayed "outside
the stable gate, afternoon" in 6/6 samples, and a reply's *suggestion* was recorded as done.
The exchange now comes first and the old scene last, "for reference only": 3/3 correct, and
both test transcripts replayed cleanly. Known limit: a place a reply dwells on at length
("I've always loved that little cabin by the woods...") can be taken for the location even
while they stand at the stables -- a "talked-about places don't count" rule and sending the
untrimmed reply both failed to fix it (3/3 still said the cabin). The scene bar is the fix. Example weather phrases
in the prompt were parroted and drifted, so weather is mostly indoors/outdoors -- editable.

**Facts are a second pass of the fold, in lockstep with it.** `session.facts` is a bullet
list rewritten (not appended) on each *successful* fold, from the same turns, so a rejected
fold retries both together. A failed fact pass keeps the old list and never undoes the fold.
Lines are judged one by one (`parse_facts`: dialogue, stage directions, first/second person,
the user's interior life, duplicates) -- one bad line costs that line, not the list. The
prompt prefills "- " for the same reason the summary prefills "Memory log:". Prototyped on
Stheno against a transcript whose porch scene the prose summary had dropped: 3 runs, 11-12
clean lines each, all keeping the cabin, the patrols and Tommy.

**A fact rewrite cannot drop an old fact (`merge_facts`).** The single pass is fine; the
sequence is not. Replaying one live chat as four folds with three seeds, the five key facts
ended 4/0/4 under the plain rewrite -- one pass took a list from 4 to 0. Tried and rejected:
a tighter "facts a reader could check, skip feelings" prompt (key retention fell to 2/5),
"never drop a promise, offer or object" (5/0/4), temperature 0 (0/5), and extracting only
new facts with a code-side append (scenery over substance, more invention, 0-2/5). What
works is restoring any old line the rewrite doesn't cover (content-word overlap); key facts
then never decreased across folds. Over the cap the least *specific* line goes
(`specificity`: other names, commitment words, days, digits up; mood/symbolism words down).
Evicting simply the oldest non-mood line was tried live and lost the earliest key facts --
Tommy's patrol fell out at turn 20 -- because the filler was symbolism, not mood words.
Known limit: a fact the model never writes down can't be restored, and filler still fills
the list -- an 8B's sense of what matters is the bottleneck, not the prompt.

**Pins enter the prompt only once folded.** A pending pinned message is already in the
history verbatim, so the "Moments to keep in mind" block holds only pins at or below the
watermark, in story order, under `PINNED_BUDGET_TOKENS` (newest kept when over). Retrieval
excludes pinned ids so the same text never appears twice. Motivated live: asked about a
porch scene the summary had skipped, the character invented one.

**Stop keeps the partial reply.** A client disconnect already ends generation on the GPU:
Starlette cancels the streaming generator, which closes the httpx stream to Ollama, which
stops. Measured on the live box -- a probe call answered 0.1 s after Stop. (An older note
here said the backend ran on; it doesn't.) What was missing is the text: cancellation used
to skip persistence, leaving a user message with no reply. The orchestrator now catches
`CancelledError`/`GeneratorExit`, guards and saves the partial (as a swipe when re-rolling),
then re-raises. No awaits in that handler -- a closing async generator may not suspend.

**Swipes live on the last reply's row, not as sibling messages.** `alternatives` holds every
version and `content` is always the selected one, so prompt building, summaries, retrieval
and both guards read `content` and never learn swipes exist; embeddings re-index on their
content hash. Regenerate no longer deletes first (a failed generation used to lose the
reply): it builds the prompt with that reply excluded, then appends. Only the latest reply
can be swiped -- once answered, swapping it would contradict the turns built on it.

**Token budgeting learns each model's chars/token instead of using a tokenizer.** The fixed
3.6 was 35% high for Llama 3 -- eight live prompts measured 4.83-4.86 (5.1 when short), so a
prompt counted at ~7,300 was really ~5,400 and ~1,900 tokens of history went unused every
turn. Ollama's `prompt_eval_count` is the whole prompt, cache included (same prompt twice:
5,280 both times), so after each reply `tokens.observe` folds it into a per-model running
ratio, used at 95% with the 256-token reserve on top. Clamped to 2.5-6; prompts under 200
tokens are ignored; in memory only, so the first turn after a restart budgets at 3.6 again.
Shipping a vocab per model was rejected as a dependency this project avoids.

**Sampler options are always sent, `min_p: 0` included.** An omitted option falls back to the
Modelfile's value, so the UI would stop being the source of truth.

**Narration of the user is stripped before storing (`chat/guard.py`), not just prompted
against.** Same shape as the summary guards. On Stheno, two runs at identical settings
narrated the user in 4 and 9 of 16 turns: the spread is feedback, not noise -- one
"*Riley nods.*" in history and the model copies its own transcript. Stripping on write breaks
the loop. The guard is deliberately narrow (narration only, user as sentence subject only,
quotes only when attached to a removed sentence); its tests are both directions. The UI
reloads the stored message at the end of a turn, so the streamed draft is replaced.
Two narrow additions, both from the live group chat: a quote opening the next paragraph
after a removed sentence that ended its own, and a sentence led by the user's voice, face or
expression ("Riley's expression softened...") together with the unattributed quote after
it -- never the possessive sentence alone. Replayed over all 146 stored replies on the test
box: no change (no false positives). Over 16 fresh raw Stheno replies: one extra cut, and it
was the user's line.
Then the pronoun case (`"Yeah, good times," he acknowledged`): cut only when the pronoun is
the user's and not the character's, both read off the persona and card (`infer_pronoun`,
subject forms only -- with object forms the live persona's "help her rebuild her life"
made Riley unclear), and the last name before the quote, outside speech, is the user's.
Any other capitalised word in between keeps it, so an unknown NPC or a place errs safe.
Over the same 146 stored replies: three cuts, all three the user's lines; no change in 16
fresh raw replies.

**A reply that slips into first-person narration is regenerated once (`redo_first_person`).**
Same feedback loop as narrating the user: ~1 Stheno reply in 5 slips, and every reply after a
stored slip followed it (13 and 16 of 16 turns in two runs). One redo before storing took two
runs to 0 of 16, firing once in 32 turns. The card's greeting is the reference -- a card that
greets in first person is never redone -- and the second attempt is kept whatever it is, so
the loop cannot spin. The UI drops its streamed draft on the `redo` event.
Two refinements from the live box: when both attempts slip, the one with fewer first-person
words is kept; and once the last saved reply is already first person the redo is skipped.
After a double slip on turn one (about 1 in 25 at the measured rate) the redo fired on every
later turn, doubling GPU time, and never pulled the chat back. Five fresh first replies on
the same build: one redo, all five saved in third person -- the double slip was luck, not a
regression.

**A reply the guard guts is regenerated once; one it trims is not.** Over 288 Stheno replies
the guard touched 60, removing a median of 11%, and only one lost more than a third (still
1,200 characters) -- so "share removed" is the wrong trigger. `_gutted` fires when the reply
is empty or under half its raw length. It shares the two-attempt budget with
`redo_first_person`. An empty result used to save nothing silently; it now raises an
`error` event telling the user to regenerate.

**Never name the behaviour you are forbidding.** A tail reminder that said "never write
{user} nodding, smiling, laughing or answering" took a chat from 3 user-narrating turns to
13 of 16, and lengthened replies -- naming the actions primed them. The reminder now says
where to stop ("end on {char}'s own words or actions") instead. Same lesson as the
summariser rule that did nothing; small models follow examples, not prohibitions.

**Chat-format fold prompts restate the task after the transcript and prefill "Memory
log:".** Seen live on Stheno: three folds in a row rejected, so a long chat had no memory at
all -- the guards worked, the prompt didn't. Five seeded folds per variant: plain prompt
1/5 accepted (scene rewrites in quotes and first person), restated 3/5, restated + prefill
3/5 with no scene rewrites left; the remaining rejects are `narrates_user`, which the next
turn's retry absorbs. Alpaca's fold prompt is untouched -- it was measured working on the
models it serves.

**`narration_style` is appended like the impersonation guard, not folded into
`DEFAULT_SYSTEM`.** A card's `system_prompt` replaces the default wholesale, so anything only
in the default disappears for exactly the cards that bring their own. A one-line pointer is
repeated in the tail for recency; the full text is not, so it doesn't crowd the card's own
`post_history_instructions`.

**Auth is middleware over `/api`, not a per-route dependency.** The guarantee wanted is
"nothing under `/api` answers without a session", and that is only true if it cannot be
forgotten on a route added later. Static files stay public deliberately — the login screen has
to load from somewhere, and the bundle holds no chat data.

**Auth is off until a password is set.** An upgrade must not lock anyone out of their own
chats, so `is_enabled()` is simply "has a hash been stored". The settings panel warns in amber
while it is unset; that nag is the other half of the trade.

**Sessions are opaque random tokens stored hashed, not signed cookies.** A signed cookie needs
a server secret that survives restarts — another thing to generate, persist and rotate. A
random token is its own secret, revocable by deleting a row, and storing only its SHA-256
means a database leak cannot be replayed. Passwords use `hashlib.scrypt`; adding bcrypt or
argon2 for one call is not worth a dependency in a project that has almost none.

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

**`post_history_instructions` is its own block** -- `### Instruction:` in Alpaca, a late
system turn in chat formats. A bare paragraph between two `### Response:` headers confuses
Llama-2 instruct tunes.

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
- **No persona means the user is "You"**, and `substitute()` turns `{{user}}'s` into "your"
  for that case only -- cards are written for a name, and "the knock on You's door" was seen
  live.
- **A card's `system_prompt` can undo the third-person default.** The Dina card's "speak and
  behave naturally rather than narrating {char}" replaces `DEFAULT_SYSTEM`, and on Stheno
  the narrator drifted into first person mid-chat. `narration_style` can restore it
  server-wide, but the card is the real fix.
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

## Testing on the live hardware

There is a separate test instance (`deploy/unraid/docker-compose.test.yml`, project
`roleplay-test`, port 8001, `:test` image from the `test` branch). **Run test chats there,
never on production.** Production holds the owner's real conversations; the owner asked
that test sessions neither read nor edit them, and the test stack enforces it by not
mounting the production volume -- a test there pins that.

- Ship changes to the test instance first: push to `test`, wait for the image, ask the
  owner to **Update Stack** on `roleplay-test`, then test. `master` only after that.
- Test data (characters, personas, chats) lives only in the test instance; seed it there
  through the API.
- Ollama on :11434 is shared and holds no chat data, so offline prompt experiments may
  call it directly. Keep runs short: they queue in front of the owner's replies.

**Settings -> Test this model** (`POST /api/eval`, `app/eval.py`) is the in-app version of the
scripts used for every measurement above: 12 generic turns through the real `stream_reply`
(guard, both redos, memory) in a temporary session deleted in a `finally`. The turns are
card-agnostic and include thin ones ("*I nod.*", "...") because those tempt impersonation.
Its health flags use this file's thresholds. It shares the GPU, so it is a manual action.

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

- **Branching.** Swipes cover the last reply (below); full alternate timelines -- going back
  to an old turn and forking -- still mean a parent pointer on `Message` and a tree UI.
- **Session export/import** as a JSON bundle, so a chat can move between installs.
- **Semantic lorebook matching.** Embedding `character_book` entries would catch paraphrases
  keywords miss. It must be *additive* to keyword matching, not a replacement — the V2 spec
  is keyword-based and imported cards depend on that behaviour. `rag.py` already has the
  embedding plumbing; the work is a second candidate source in `lorebook.select` and a
  policy for combining the two signals.

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
- **L3-8B-Stheno-v3.2 (Q4_K_M) was measured offline, not yet through a deployed build.** Three
  cards x 16 scripted turns per configuration, rendered by this repo's builder and sent raw to
  the user's Ollama (RTX 2080) -- no summary or retrieval in the loop. With `llama3` format,
  temperature 1.12 / min_p 0.075 / top_k 50 / top_p 1, a narration style and the guard: no
  replies hit the length cap, ~4-9 s per reply, stored replies narrating the user fell to
  0-2 of 16 per chat. One card (Dina) still drifted into first person in 2 of 3 runs; that
  traces to its own `system_prompt`, not the builder. The live app has not run these
  changes yet.
- **Only one card has been exercised**, and it turned out to be misconfigured (its `name`
  was the *user's* role — see Gotchas). Card compatibility across a library is untested.
- **The Ollama `/api/embed` → `/api/embeddings` fallback** is still reasoned from API
  history, not observed against an older Ollama.
- **The XML templates** have not been imported into a live Docker tab; only the Compose
  Manager path has been walked.
