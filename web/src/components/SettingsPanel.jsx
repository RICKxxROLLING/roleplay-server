import { useEffect, useRef, useState } from "react";
import { api, streamTurn } from "../api";
import { Input, Textarea } from "./ui";

const SLIDERS = [
  { key: "temperature", min: 0.1, max: 2, step: 0.05, hint: "Higher = more creative, less coherent" },
  { key: "top_p", min: 0.1, max: 1, step: 0.05, hint: "Nucleus sampling cutoff" },
  { key: "top_k", min: 0, max: 200, step: 1, hint: "0 disables top-k" },
  { key: "min_p", min: 0, max: 0.3, step: 0.005, hint: "Drops unlikely words relative to the best one; keeps high temperatures coherent. 0 disables" },
  { key: "repeat_penalty", min: 1, max: 1.5, step: 0.01, hint: "Curbs looping; >1.2 can flatten voice" },
  { key: "repeat_last_n", min: 64, max: 2048, step: 64, hint: "How far back that penalty looks. 64 is shorter than one reply, which is what makes phrasing recycle across turns" },
  { key: "max_new_tokens", min: 64, max: 1024, step: 16, hint: "Reply length cap" },
];

const PROMPT_FORMATS = [
  { value: "alpaca", label: "Alpaca (Mistral / Llama 2 merges)" },
  { value: "llama3", label: "Llama 3 (Stheno, Lumimaid, L3 tunes)" },
  { value: "chatml", label: "ChatML (Qwen, many Nemo tunes)" },
];

const pickClass = "w-full bg-ink-950 border border-ink-800 rounded-lg px-2 py-1.5 text-sm outline-none focus:border-accent/60";

/** Flux: guidance, steps, and the files when it isn't one checkpoint (app/images.py). */
function FluxOptions({ s, update, state }) {
  const pick = (key, label, list, blank) => (
    <select value={s[key] ?? ""} onChange={(e) => update(key, e.target.value)} aria-label={label} className={pickClass}>
      {blank && <option value="">{blank}</option>}
      {!list.includes(s[key]) && s[key] && <option value={s[key]}>{s[key]}</option>}
      {list.map((f) => <option key={f} value={f}>{f}</option>)}
    </select>
  );
  return (
    <div className="space-y-2 pl-3 border-l border-ink-800">
      <div className="flex gap-3 text-sm">
        <label className="flex items-center gap-2">Guidance
          <input type="number" min={1} max={10} step={0.5} value={s.flux_guidance ?? 3.5}
                 onChange={(e) => update("flux_guidance", Number(e.target.value))}
                 className="w-16 bg-ink-950 border border-ink-800 rounded px-1.5 py-1 text-sm" />
        </label>
        <label className="flex items-center gap-2">Steps
          <input type="number" min={1} max={60} value={s.flux_steps ?? 20}
                 onChange={(e) => update("flux_steps", Number(e.target.value))}
                 className="w-16 bg-ink-950 border border-ink-800 rounded px-1.5 py-1 text-sm" />
        </label>
      </div>
      <p className="text-xs text-slate-600">20 steps for dev, 4 for schnell. With the model as one checkpoint file,
        pick it above and leave the next box on "One checkpoint file".</p>
      {pick("flux_unet", "Flux diffusion model", state?.unets || [], "One checkpoint file")}
      {(s.flux_unet ?? "") !== "" && (
        <>
          {pick("flux_clip_l", "CLIP-L text encoder", state?.text_encoders || [])}
          {pick("flux_t5", "T5 text encoder", state?.text_encoders || [])}
          {pick("flux_vae", "VAE", state?.vaes || [])}
        </>
      )}
    </div>
  );
}

/** LoRAs for pictures: a file ComfyUI lists, how strong, when it's used, its trigger words. */
function Loras({ s, update, files }) {
  const list = s.image_loras || [];
  const save = (next) => update("image_loras", next);
  const set = (i, patch) => save(list.map((l, k) => (k === i ? { ...l, ...patch } : l)));
  return (
    <div className="space-y-2">
      <div className="text-sm">LoRAs</div>
      {list.map((l, i) => (
        <div key={i} className="space-y-1.5 p-2 border border-ink-800 rounded-lg">
          <select value={l.file} onChange={(e) => set(i, { file: e.target.value })} aria-label="LoRA file" className={pickClass}>
            {!files.includes(l.file) && <option value={l.file}>{l.file}</option>}
            {files.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          <div className="flex flex-wrap gap-2 items-center text-sm">
            <select value={l.use || "always"} onChange={(e) => set(i, { use: e.target.value })} aria-label="When"
                    className="bg-ink-950 border border-ink-800 rounded px-1.5 py-1 text-sm">
              <option value="always">Every picture</option>
              <option value="scenes">Scenes</option>
              <option value="selfies">Selfies</option>
              <option value="portraits">Portraits</option>
            </select>
            <label className="flex items-center gap-1.5">Strength
              <input type="number" min={-2} max={2} step={0.05} value={l.strength ?? 1}
                     onChange={(e) => set(i, { strength: Number(e.target.value) })}
                     className="w-16 bg-ink-950 border border-ink-800 rounded px-1.5 py-1 text-sm" />
            </label>
            <button onClick={() => save(list.filter((_, k) => k !== i))} className="text-xs text-slate-500 hover:text-rose-300 ml-auto">
              remove
            </button>
          </div>
          <Input
            aria-label="Trigger words"
            placeholder="Trigger words from its page, if it has any"
            defaultValue={l.trigger || ""}
            onBlur={(e) => e.target.value !== (l.trigger || "") && set(i, { trigger: e.target.value })}
          />
        </div>
      ))}
      {files.length > 0 ? (
        list.length < 8 && (
          <button onClick={() => save([...list, { file: files[0], strength: 1, use: "selfies", trigger: "" }])}
                  className="text-sm text-accent hover:underline">
            Add a LoRA
          </button>
        )
      ) : (
        <p className="text-xs text-slate-600">Put LoRA files in ComfyUI's models/loras folder and they'll show here.
          A LoRA only works with the kind of model it was made for.</p>
      )}
    </div>
  );
}

/**
 * Pictures come from ComfyUI on another machine on the home network (app/images.py). The
 * address is saved on blur, then checked, so the panel says straight away whether it answers.
 */
function Pictures({ s, setS, update }) {
  const [state, setState] = useState(null);
  const check = () => api.pictureStatus().then(setState).catch((e) => setState({ ok: false, error: e.message, checkpoints: [] }));
  useEffect(() => {
    check();
  }, []);
  const flux = (s.image_family ?? "sdxl") === "flux";
  return (
    <>
      <Section title="Connection" hint="ComfyUI on your PC. It has no password, so keep it on your home network only.">
        <Input
          id="comfyui-url"
          aria-label="ComfyUI address"
          value={s.comfyui_url ?? ""}
          placeholder="ComfyUI address: http://10.2.1.123:8188"
          onChange={(e) => setS((prev) => ({ ...prev, comfyui_url: e.target.value }))}
          onBlur={async (e) => {
            await update("comfyui_url", e.target.value.trim());
            check();
          }}
        />
        <Input
          id="comfyui-waker"
          aria-label="Helper that starts ComfyUI"
          value={s.comfyui_waker_url ?? ""}
          placeholder="Helper, to start it on demand: http://10.2.1.123:8190"
          onChange={(e) => setS((prev) => ({ ...prev, comfyui_waker_url: e.target.value }))}
          onBlur={async (e) => {
            await update("comfyui_waker_url", e.target.value.trim());
            check();
          }}
        />
        {state && (
          <p className={`text-xs ${state.ok || state.asleep ? "text-emerald-300" : "text-slate-500"}`}>
            {state.ok ? `Connected. ${state.checkpoints.length} model${state.checkpoints.length === 1 ? "" : "s"} found.` : state.error}
          </p>
        )}
      </Section>

      <Section title="Model">
        <select
          value={s.image_family ?? "sdxl"}
          onChange={(e) => update("image_family", e.target.value)}
          aria-label="Kind of picture model"
          className={pickClass}
        >
          <option value="sdxl">SDXL model (Juggernaut XL and the like)</option>
          <option value="flux">Flux model (FLUX.1 dev or schnell)</option>
        </select>
        {((state?.checkpoints || []).length > 0 || s.comfyui_checkpoint) && (
          <select
            value={s.comfyui_checkpoint ?? ""}
            onChange={(e) => update("comfyui_checkpoint", e.target.value)}
            aria-label="Picture model"
            className={pickClass}
          >
            <option value="">First one found</option>
            {/* While ComfyUI is stopped the list can be empty; the chosen one still shows. */}
            {s.comfyui_checkpoint && !(state?.checkpoints || []).includes(s.comfyui_checkpoint) && (
              <option value={s.comfyui_checkpoint}>{s.comfyui_checkpoint}</option>
            )}
            {(state?.checkpoints || []).map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        )}
        {flux && <FluxOptions s={s} update={update} state={state} />}
        {!flux && (
          <Toggle checked={s.image_detail ?? true} onChange={(v) => update("image_detail", v)} title="Sharper faces">
            A second pass at a larger size, so eyes and faces come out clean. About twice as slow.
          </Toggle>
        )}
      </Section>

      <Section title="Look">
        <Input
          aria-label="Picture style"
          value={s.image_style ?? ""}
          placeholder="Style: detailed digital painting, cinematic lighting, high quality"
          onChange={(e) => setS((prev) => ({ ...prev, image_style: e.target.value }))}
          onBlur={(e) => update("image_style", e.target.value.trim())}
        />
        <p className="text-xs text-slate-600">Words that end every scene and portrait. For photos, try
          "ultra-realistic photograph, natural light, sharp focus". Selfies always look like phone photos.</p>
        <Loras s={s} update={update} files={state?.loras || []} />
        {!flux && (
          <>
            <label htmlFor="image-negative" className="text-sm block">Leave out of pictures</label>
            <Textarea
              id="image-negative"
              rows={2}
              maxLength={1000}
              value={s.image_negative ?? ""}
              onChange={(e) => setS((prev) => ({ ...prev, image_negative: e.target.value }))}
              onBlur={(e) => update("image_negative", e.target.value)}
            />
          </>
        )}
        {flux && (
          <p className="text-xs text-slate-600">Flux has no "leave out" list; the server refuses explicit
            pictures itself unless Safety allows them.</p>
        )}
      </Section>

      <Section title="Selfies">
        <Toggle checked={s.texting_selfies ?? true} onChange={(v) => update("texting_selfies", v)} title="Selfies when you text for one">
          In texting mode, "send me a pic" gets a phone selfie back. Ask for a selfie is in the scene panel too.
        </Toggle>
      </Section>
    </>
  );
}

const TABS = [
  { id: "model", label: "Model" },
  { id: "story", label: "Story" },
  { id: "memory", label: "Memory" },
  { id: "pictures", label: "Pictures" },
  { id: "safety", label: "Safety" },
  { id: "account", label: "Account" },
];

/** A titled group of settings inside a tab. */
function Section({ title, hint, children }) {
  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</h3>
        {hint && <p className="text-xs text-slate-600 mt-0.5">{hint}</p>}
      </div>
      {children}
    </section>
  );
}

/** A switch with a title and an explanation under it. */
function Toggle({ checked, onChange, title, children }) {
  return (
    <label className="flex items-start gap-2.5 text-sm cursor-pointer">
      <input type="checkbox" checked={!!checked} onChange={(e) => onChange(e.target.checked)}
             className="accent-accent w-4 h-4 mt-0.5 shrink-0" />
      <span>
        {title}
        {children && <span className="block text-xs text-slate-600">{children}</span>}
      </span>
    </label>
  );
}

function Slider({ label, value, shown, min, max, step, onChange, hint }) {
  return (
    <div>
      <div className="flex justify-between text-sm mb-1">
        <label>{label}</label>
        <span className="text-accent tabular-nums">{shown ?? value}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
             onChange={(e) => onChange(Number(e.target.value))} className="w-full accent-accent" />
      {hint && <p className="text-xs text-slate-600 mt-0.5">{hint}</p>}
    </div>
  );
}

function readTab() {
  try {
    return localStorage.getItem("settings-tab") || "model";
  } catch {
    return "model";
  }
}

export default function SettingsPanel({ open, onClose, sessionId, onOpenModels }) {
  const [s, setS] = useState(null);
  const [prompt, setPrompt] = useState(null);
  const [tab, setTabState] = useState(readTab);
  const setTab = (id) => {
    setTabState(id);
    try {
      localStorage.setItem("settings-tab", id);
    } catch {
      /* a remembered tab is a convenience only */
    }
  };

  useEffect(() => {
    if (open) api.settings().then(setS).catch(() => {});
  }, [open]);

  if (!open || !s) return null;

  async function update(key, value) {
    setS((prev) => ({ ...prev, [key]: value }));
    try {
      await api.patchSettings({ [key]: value });
    } catch {
      /* keep local value; server will resync on reopen */
    }
  }

  // grid-cols-1 makes the column minmax(0, 1fr): without it the row of tabs, which doesn't wrap,
  // sized the panel to 472px on a 375px phone.
  return (
    <div className="fixed inset-0 bg-black/60 grid grid-cols-1 place-items-center z-50 p-2 sm:p-4" onClick={onClose}>
      <div
        className="bg-ink-900 border border-ink-700 rounded-xl w-full min-w-0 max-w-[600px] max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 pt-4 pb-2 flex items-center justify-between">
          <h2 className="font-semibold">Settings</h2>
          <button onClick={onClose} aria-label="Close settings" className="text-slate-500 hover:text-slate-300 text-xl leading-none">×</button>
        </div>
        <nav className="px-3 border-b border-ink-800 flex gap-1 overflow-x-auto overflow-y-hidden [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className={`shrink-0 px-3 pt-2 pb-2.5 text-sm leading-5 border-b-2 ${
                tab === t.id ? "border-accent text-slate-100" : "border-transparent text-slate-500 hover:text-slate-300"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>

        <div className="p-5 space-y-7 overflow-y-auto scroll-thin">
          <p className="text-xs text-slate-600 -mb-3">Changes save as you make them and survive restarts.</p>

          {tab === "model" && (
            <>
              <Section title="Chat model" hint="The prompt format, context and samplers are saved per model and come back when you switch to it.">
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <span>
                    Backend <span className="text-slate-300">{s.backend}</span> · model{" "}
                    <span className="text-slate-300">{s.model}</span> · context{" "}
                    <span className="text-slate-300">{s.context_tokens}</span> tokens
                  </span>
                  <button onClick={onOpenModels} className="ml-auto shrink-0 border border-ink-700 rounded-lg px-2 py-1 hover:bg-ink-850">
                    Change model
                  </button>
                </div>
                <div>
                  <div className="flex justify-between items-center gap-3 text-sm mb-1">
                    <label htmlFor="prompt-format">Prompt format</label>
                    <select
                      id="prompt-format"
                      value={s.prompt_format}
                      onChange={(e) => update("prompt_format", e.target.value)}
                      className="w-full sm:w-auto bg-ink-950 border border-ink-800 rounded-lg px-2 py-1 text-sm outline-none focus:border-accent/60"
                    >
                      {PROMPT_FORMATS.map((f) => (
                        <option key={f.value} value={f.value}>{f.label}</option>
                      ))}
                    </select>
                  </div>
                  <p className="text-xs text-slate-600 mt-0.5">
                    Must match how the model was trained — check its model card. The wrong one still
                    answers, but thinner, and drifts as a chat gets long.
                  </p>
                </div>
              </Section>

              <Section title="Samplers" hint="How the model picks its words.">
                {SLIDERS.map(({ key, min, max, step, hint }) => (
                  <Slider key={key} label={<span className="capitalize">{key.replace(/_/g, " ")}</span>} value={s[key]}
                          min={min} max={max} step={step} hint={hint} onChange={(v) => update(key, v)} />
                ))}
              </Section>

              <Section title="Check">
                <TestModelSection />
              </Section>
            </>
          )}

          {tab === "story" && (
            <>
              <Section title="Writing">
                <div>
                  <label htmlFor="narration-style" className="text-sm block mb-1">Narration style</label>
                  {/* Saved on blur: a PATCH per keystroke would race itself. */}
                  <Textarea
                    id="narration-style"
                    rows={4}
                    maxLength={2000}
                    value={s.narration_style ?? ""}
                    onChange={(e) => setS((prev) => ({ ...prev, narration_style: e.target.value }))}
                    onBlur={(e) => update("narration_style", e.target.value)}
                    placeholder="e.g. Describe the setting, weather, sounds and background characters in every reply."
                  />
                  <p className="text-xs text-slate-600 mt-0.5">
                    Added to every character's prompt after its own instructions. Use {"{{char}}"} and {"{{user}}"} for
                    names. Leave blank to add nothing.
                  </p>
                </div>
                <Toggle checked={s.slow_pacing} onChange={(v) => update("slow_pacing", v)} title="Live in the moment">
                  Slower scenes and shorter replies; you decide when they move on.
                </Toggle>
                <Toggle checked={s.redo_first_person} onChange={(v) => update("redo_first_person", v)} title="Redo replies that slip into first person">
                  When a third-person character starts narrating as "I", the reply is written again once.
                </Toggle>
                <Toggle checked={s.tidy_user_lines ?? true} onChange={(v) => update("tidy_user_lines", v)} title="Add quotes and asterisks to what you type">
                  Your speech in quotes and your actions in asterisks, so the character can tell them apart. Your words
                  are never changed; if they would be, your line is kept exactly as typed.
                </Toggle>
              </Section>

              <Section title="The world around you">
                <Toggle checked={s.scene_enabled} onChange={(v) => update("scene_enabled", v)} title="Track the scene">
                  Where, when, weather and who is present · under a second per reply.
                </Toggle>
                <Toggle checked={s.narrator_default} onChange={(v) => update("narrator_default", v)} title="Narrator in new chats">
                  A short line about the world every few messages · about 2 s.
                </Toggle>
                <Toggle checked={s.thoughts_enabled} onChange={(v) => update("thoughts_enabled", v)} title="Their own thoughts">
                  A private journal and opinion of you, after each reply · about 2 s.
                </Toggle>
              </Section>

              <Section title="Characters writing first">
                <AutonomySettings s={s} update={update} />
              </Section>

              <Section title="Reading aloud" hint="On the CPU, so chat isn't slowed.">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <select
                    value={s.voices_mode}
                    onChange={(e) => update("voices_mode", e.target.value)}
                    className="bg-ink-950 border border-ink-800 rounded-lg px-2 py-1 text-sm outline-none focus:border-accent/60"
                  >
                    <option value="dialogue">Only what they say</option>
                    <option value="story">Everything, narration in the narrator's voice</option>
                    <option value="everything">Everything, in their voice</option>
                  </select>
                  <select
                    value={s.voices_speed}
                    onChange={(e) => update("voices_speed", Number(e.target.value))}
                    className="bg-ink-950 border border-ink-800 rounded-lg px-2 py-1 text-sm outline-none focus:border-accent/60"
                  >
                    {[0.8, 0.9, 1, 1.1, 1.2, 1.35].map((n) => (
                      <option key={n} value={n}>{n}x</option>
                    ))}
                  </select>
                </div>
              </Section>
            </>
          )}

          {tab === "memory" && (
            <>
              <Section title="Summary" hint="Older turns are condensed so a long chat fits the context window.">
                <Toggle checked={s.summary_enabled} onChange={(v) => update("summary_enabled", v)} title="Rolling summarization" />
                {s.summary_enabled && (
                  <>
                    <Toggle checked={s.facts_enabled} onChange={(v) => update("facts_enabled", v)} title="Keep a fact list">
                      Names, objects and promises beside the summary · one extra generation per condense.
                    </Toggle>
                    <Slider label="Fold threshold" value={s.summary_trigger_tokens} shown={`${s.summary_trigger_tokens} tokens`}
                            min={400} max={3000} step={100} onChange={(v) => update("summary_trigger_tokens", v)}
                            hint="Condense once un-summarized history passes this size" />
                    <Slider label="Keep verbatim" value={s.keep_recent_messages} shown={`${s.keep_recent_messages} messages`}
                            min={2} max={30} step={1} onChange={(v) => update("keep_recent_messages", v)}
                            hint="Recent turns never get condensed" />
                  </>
                )}
              </Section>

              <Section title="Vector recall" hint={s.retrieval_enabled ? "Tuning. The on/off switch is in the Memory panel." : "Off. Turn it on in a chat's Memory panel to tune it here."}>
                {s.retrieval_enabled && (
                  <>
                    <Slider label="Moments recalled" value={s.retrieval_top_k} min={1} max={10} step={1}
                            onChange={(v) => update("retrieval_top_k", v)} hint="Most past turns re-injected per reply" />
                    <Slider label="Relevance floor" value={s.retrieval_min_score} min={0.1} max={0.9} step={0.05}
                            onChange={(v) => update("retrieval_min_score", v)}
                            hint="Raise it if the character brings up unrelated old scenes; lower it if it fails to remember things it should" />
                    <Slider label="Recall budget" value={s.retrieval_budget_tokens} shown={`${s.retrieval_budget_tokens} tokens`}
                            min={100} max={1200} step={50} onChange={(v) => update("retrieval_budget_tokens", v)}
                            hint="Context spent on recall, taken from what history could use" />
                  </>
                )}
              </Section>

              {sessionId && (
                <Section title="This chat's prompt" hint="Exactly what the model will be sent next, for checking.">
                  <PromptInspector sessionId={sessionId} prompt={prompt} setPrompt={setPrompt} />
                </Section>
              )}
            </>
          )}

          {tab === "pictures" && <Pictures s={s} setS={setS} update={update} />}

          {tab === "safety" && <SafetySection s={s} update={update} />}

          {tab === "account" && (
            <>
              <PasswordSection />
              <TwoFactorSection />
              <NotificationsSection />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function PromptInspector({ sessionId, prompt, setPrompt }) {
  return (
    <div>
      <button
        onClick={async () => setPrompt(await api.inspectPrompt(sessionId))}
        className="text-sm rounded-lg border border-ink-700 px-3 py-1.5 hover:bg-ink-850"
      >
        Inspect built prompt
      </button>
      {prompt && (
        <div className="mt-3">
          <div className="text-xs text-slate-500 mb-1">
            ~{prompt.estimated_tokens} tokens
            {prompt.chars_per_token &&
              ` (${prompt.chars_per_token} chars/token, ${prompt.chars_per_token_measured ? "measured" : "default until the first reply"})`}
            {" · "}{prompt.dropped_messages} messages trimmed · {prompt.lore_entries ?? 0} lore entries
            {prompt.lore_dropped > 0 && ` (${prompt.lore_dropped} over budget)`}
            {" · "}
            {prompt.pinned_entries ?? 0} pinned
            {prompt.pinned_dropped > 0 && ` (${prompt.pinned_dropped} over budget)`}
            {" · "}
            {prompt.retrieved_entries ?? 0} recalled
            {prompt.retrieval_dropped > 0 && ` (${prompt.retrieval_dropped} over budget)`}
          </div>
          {prompt.retrieval_error && (
            <div className="text-xs text-amber-400/90 mb-1">Vector recall unavailable: {prompt.retrieval_error}</div>
          )}
          {prompt.retrieved?.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-2">
              {prompt.retrieved.map((h) => (
                <span key={h.message_id} title={h.content}
                      className="text-[10px] px-2 py-0.5 rounded-full bg-sky-500/15 border border-sky-500/25 text-sky-300/90">
                  #{h.message_id} · {h.score.toFixed(2)}
                </span>
              ))}
            </div>
          )}
          {prompt.lore_fired?.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-2">
              {prompt.lore_fired.map((f, i) => (
                <span key={i} className="text-[10px] px-2 py-0.5 rounded-full bg-accent/15 border border-accent/25 text-accent/90">
                  {f}
                </span>
              ))}
            </div>
          )}
          <pre className="text-[11px] bg-ink-950 border border-ink-800 rounded-lg p-3 max-h-64 overflow-auto scroll-thin whitespace-pre-wrap">
            {prompt.prompt}
          </pre>
        </div>
      )}
    </div>
  );
}

/**
 * Set, change or clear the password.
 *
 * Lives in settings rather than a separate screen because it is a property of
 * the server, not of a user -- there is only ever one. The warning when it is
 * unset is deliberately blunt: an open server means anyone who reaches the
 * port can read every chat.
 */
function PasswordSection() {
  const [status, setStatus] = useState(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);

  useEffect(() => {
    api.authStatus().then(setStatus).catch(() => {});
  }, []);

  if (!status) return null;

  async function save(value) {
    setBusy(true);
    setNote(null);
    try {
      const r = await api.setPassword(value, current || undefined);
      setCurrent("");
      setNext("");
      if (r.enabled) {
        // Every session was revoked, including this one -- reload straight
        // into the login screen rather than leaving a dead page up.
        window.location.reload();
      } else {
        setStatus({ ...status, enabled: false });
        setNote("Password removed. The server is open again.");
      }
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pt-3 border-t border-ink-800 space-y-3">
      <div className="text-sm">
        Password
        <span className="text-xs text-slate-600 ml-2">
          required before any chat can be read
        </span>
      </div>

      {!status.enabled && (
        <div className="text-xs text-amber-400/90 bg-amber-950/30 border border-amber-900/40 rounded-lg px-3 py-2">
          No password set — anyone who can reach this server can read and delete
          every chat.
        </div>
      )}

      {status.enabled && (
        <input
          type="password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          placeholder="Current password"
          autoComplete="current-password"
          className="w-full bg-ink-950 border border-ink-800 rounded-lg px-3 py-1.5 text-sm outline-none focus:border-accent/60 placeholder:text-slate-600"
        />
      )}

      <input
        type="password"
        value={next}
        onChange={(e) => setNext(e.target.value)}
        placeholder={status.enabled ? "New password" : "Set a password (8+ characters)"}
        autoComplete="new-password"
        className="w-full bg-ink-950 border border-ink-800 rounded-lg px-3 py-1.5 text-sm outline-none focus:border-accent/60 placeholder:text-slate-600"
      />

      <div className="flex items-center gap-2">
        <button
          onClick={() => save(next)}
          disabled={busy || next.length < 8 || (status.enabled && !current)}
          className="text-sm px-3 py-1.5 rounded-lg bg-accent/25 border border-accent/40 hover:bg-accent/35 disabled:opacity-40"
        >
          {status.enabled ? "Change password" : "Set password"}
        </button>
        {status.enabled && (
          <>
            <button
              onClick={() => save("")}
              disabled={busy || !current}
              className="text-sm px-3 py-1.5 rounded-lg border border-ink-700 hover:bg-ink-850 disabled:opacity-40"
            >
              Remove
            </button>
            <button
              onClick={async () => {
                await api.logout().catch(() => {});
                window.location.reload();
              }}
              className="text-sm px-3 py-1.5 rounded-lg border border-ink-700 hover:bg-ink-850"
            >
              Sign out
            </button>
          </>
        )}
        {note && <span className="text-xs text-slate-500">{note}</span>}
      </div>

      <p className="text-[11px] text-slate-600 leading-relaxed">
        One password for the whole server — there are no accounts. Changing or
        removing it signs out every device.
      </p>
    </div>
  );
}

/**
 * Characters writing first (app/chat/autonomy.py). Each chat opts in from its
 * header; these are the rules every such chat follows. Saved on blur, like
 * the narration style, so typing a time doesn't send half of one.
 */
function AutonomySettings({ s, update }) {
  const [draft, setDraft] = useState({});
  const val = (k) => (draft[k] !== undefined ? draft[k] : s[k]);
  const field = (k, props) => ({
    value: val(k) ?? "",
    onChange: (e) => setDraft({ ...draft, [k]: e.target.value }),
    onBlur: () => {
      const v = props?.number ? Number(val(k)) : val(k);
      if (v !== s[k] && v !== "" && !Number.isNaN(v)) update(k, v);
      setDraft({ ...draft, [k]: undefined });
    },
    className: "bg-ink-950 border border-ink-800 rounded px-1.5 py-0.5 text-sm outline-none focus:border-accent/60 " + (props?.w || "w-16"),
  });

  return (
    <div className="space-y-1.5">
      <div className="text-sm">
        Characters writing first
        <span className="text-xs text-slate-600 ml-2">turn on per chat, from its header</span>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-slate-400">
        <span>
          quiet from <input {...field("autonomy_quiet_start")} /> to <input {...field("autonomy_quiet_end")} />
        </span>
        <span>
          at least <input type="number" min={15} {...field("autonomy_min_gap_minutes", { number: true })} /> min
          between messages
        </span>
        <span>
          at most <input type="number" min={1} {...field("autonomy_max_per_day", { number: true, w: "w-12" })} /> a day
        </span>
      </div>
      <p className="text-[11px] text-slate-600 leading-relaxed">
        The gap doubles for each message you haven't answered, and after{" "}
        {s.autonomy_max_unanswered} unanswered they wait for you. Times are in {s.timezone || "the server's zone"}
        , taken from this browser. Each message arrives as a notification on devices where they're on.
      </p>
    </div>
  );
}

/** "Firefox on Linux", "iPhone" -- enough to tell devices apart in the list. */
function deviceLabel() {
  const ua = navigator.userAgent;
  const os = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Android/.test(ua) ? "Android"
    : /Mac OS X/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "";
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox"
    : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  return os === "iPhone" || os === "iPad" ? os : `${browser}${os ? ` on ${os}` : ""}`;
}

const b64ToBytes = (s) => {
  const raw = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
};

/**
 * Push notifications for this device (app/push.py). Pushes go out empty
 * through the browser's relay; the device then fetches what happened from
 * this server. Only possible over HTTPS, and on iPhone only from the app
 * added to the Home Screen.
 */
/**
 * Who may be romantic or pictured (app/routes/safety.py). The rules for anyone written as under
 * 18 are fixed, so they're stated here rather than offered as switches.
 */
function SafetySection({ s, update }) {
  const [people, setPeople] = useState(null);
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState(null);
  const load = () => api.safetyPeople().then(setPeople).catch((e) => setNote(e.message));
  useEffect(() => {
    load();
  }, [s.romance_mode, s.unknown_age]);

  async function tag(p, on) {
    setNote(null);
    try {
      const got = await api.setRomance(p.id, on);
      setPeople((list) => list.map((x) => (x.id === p.id ? got : x)));
    } catch (e) {
      setNote(e.message);
    }
  }

  const select = "w-full bg-ink-950 border border-ink-800 rounded-lg px-2 py-1.5 text-sm outline-none focus:border-accent/60";
  const shown = (people || []).filter((p) => !p.extra || p.romance_tag);
  const unknown = (people || []).filter((p) => p.age_unknown).length;
  const eligible = (people || []).filter((p) => p.explicit_eligible).length;

  return (
    <>
      <Section title="Always on" hint="These can't be switched off.">
        <p className="text-xs text-slate-500">
          Anyone whose card says they're under 18 stays platonic, is never pictured, is never part of
          explicit content, and is never offered romantic or physical choices — and so is everyone
          when your persona says you're under 18. People the app writes for a world are always 22 or older.
        </p>
      </Section>

      <Section title="Romance and age">
        <label className="block space-y-1">
          <span className="text-sm">Romance</span>
          <select value={s.romance_mode ?? "tagged"} onChange={(e) => update("romance_mode", e.target.value)} className={select}>
            <option value="tagged">Only characters you mark for romance</option>
            <option value="adults">Any adult character</option>
            <option value="never">Nobody</option>
          </select>
          <span className="block text-xs text-slate-600">Anyone else stays a friend and kindly says no.</span>
        </label>
        <label className="block space-y-1">
          <span className="text-sm">Cards that don't state an age</span>
          <select value={s.unknown_age ?? "adult"} onChange={(e) => update("unknown_age", e.target.value)} className={select}>
            <option value="adult">Treat them as adults</option>
            <option value="platonic">Keep them platonic and out of pictures</option>
          </select>
          <span className="block text-xs text-slate-600">
            {unknown ? `${unknown} of your characters don't state an age. ` : ""}
            Add one to the description ("Dina, 24, ...") to settle it.
          </span>
        </label>
        <Toggle checked={s.trust_enabled} onChange={(v) => update("trust_enabled", v)} title="Closeness has to be earned">
          Trust, affection and respect shape how they treat you, and romance waits until they're high · about 1 s per reply.
        </Toggle>
      </Section>

      <Section title="Explicit content" hint="Adults only, and off until you turn it on.">
        <Toggle checked={s.explicit_text} onChange={(v) => update("explicit_text", v)} title="Explicit writing">
          Intimate scenes may be written explicitly when the story goes there.
        </Toggle>
        <Toggle checked={s.explicit_pictures} onChange={(v) => update("explicit_pictures", v)} title="Explicit pictures">
          Lifts the server's own filter on explicit pictures. For SDXL models, also take "nsfw, nudity"
          out of the "leave out" list on the Pictures tab.
        </Toggle>
        <p className="text-xs text-slate-500">
          Both only ever apply to characters who may be romantic and whose card states an age of 18 or
          over{people ? ` (${eligible} right now)` : ""}; a card without an age never qualifies.
        </p>
      </Section>

      <Section title="Safeguards">
        <Toggle checked={s.guard_user_actions} onChange={(v) => update("guard_user_actions", v)} title="Keep the character from acting for you">
          Removes lines where a reply narrates your actions or speaks for you, before it's saved.
        </Toggle>
      </Section>

      <Section title="Who this applies to">
        <button onClick={() => setOpen((v) => !v)} className="text-sm text-accent hover:underline">
          {open ? "Hide" : "Show"} everyone{people ? ` (${shown.length})` : ""}
        </button>
        {open && people && (
          <ul className="divide-y divide-ink-800 border border-ink-800 rounded-lg">
            {shown.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
                <span className="flex-1 min-w-[8rem] truncate">{p.name}</span>
                <span className={`text-xs ${p.minor ? "text-amber-300" : "text-slate-500"}`}>
                  {p.minor ? "under 18" : p.age ? p.age : "no age"}
                </span>
                <span className="text-xs text-slate-500">{p.pictured ? "pictured" : "no pictures"}</span>
                <label className="flex items-center gap-1.5 text-xs" title={p.minor ? "Never, written as under 18" : ""}>
                  <input
                    type="checkbox"
                    className="accent-accent w-4 h-4"
                    disabled={p.minor || (s.romance_mode ?? "tagged") !== "tagged"}
                    checked={(s.romance_mode ?? "tagged") === "tagged" ? p.romance_tag : p.romance}
                    onChange={(e) => tag(p, e.target.checked)}
                  />
                  romance
                </label>
              </li>
            ))}
          </ul>
        )}
        {note && <p className="text-xs text-rose-300">{note}</p>}
      </Section>
    </>
  );
}

function NotificationsSection() {
  const [sub, setSub] = useState(undefined); // undefined = checking, null = off
  const [devices, setDevices] = useState([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);

  const supported = window.isSecureContext && "serviceWorker" in navigator && "PushManager" in window;
  const ios = /iPhone|iPad/.test(navigator.userAgent);
  const installed = window.matchMedia?.("(display-mode: standalone)").matches || navigator.standalone;

  const load = () => api.pushDevices().then(setDevices).catch(() => {});
  useEffect(() => {
    load();
    if (!supported) return setSub(null);
    navigator.serviceWorker.ready
      .then((reg) => reg.pushManager.getSubscription())
      .then((s) => setSub(s || null))
      .catch(() => setSub(null));
  }, []);

  async function run(fn) {
    setBusy(true);
    setNote(null);
    try {
      await fn();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(false);
    }
  }

  const turnOn = () => run(async () => {
    if ((await Notification.requestPermission()) !== "granted")
      throw new Error("Notifications are blocked for this site in the browser's settings.");
    const reg = await navigator.serviceWorker.ready;
    const { key } = await api.pushKey();
    const s = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
    await api.pushSubscribe(s.endpoint, deviceLabel());
    setSub(s);
    await load();
    setNote("On. Try a test notification.");
  });

  const turnOff = () => run(async () => {
    await api.pushUnsubscribe(sub.endpoint);
    await sub.unsubscribe();
    setSub(null);
    await load();
  });

  const button = "text-sm px-3 py-1.5 rounded-lg border border-ink-700 hover:bg-ink-850 disabled:opacity-40";
  const primary = "text-sm px-3 py-1.5 rounded-lg bg-accent/25 border border-accent/40 hover:bg-accent/35 disabled:opacity-40";

  return (
    <div className="pt-3 border-t border-ink-800 space-y-3">
      <div className="text-sm">
        Notifications
        <span className="text-xs text-slate-600 ml-2">
          {sub ? "on for this device" : "off for this device"}
        </span>
      </div>

      {!window.isSecureContext ? (
        <p className="text-xs text-slate-500">
          Notifications and installing the app need HTTPS -- open this server through its public
          (Cloudflare) address to turn them on.
        </p>
      ) : ios && !installed ? (
        <p className="text-xs text-slate-500">
          On iPhone, notifications work only from the app: tap Share → Add to Home Screen, open it
          from there, and come back here.
        </p>
      ) : !supported ? (
        <p className="text-xs text-slate-500">This browser can't receive push notifications.</p>
      ) : (
        <div className="flex items-center gap-2">
          {sub ? (
            <>
              <button className={button} disabled={busy} onClick={() => run(async () => {
                const r = await api.pushTest();
                const refused = (r.devices || []).filter((d) => !d.ok);
                setNote(
                  (r.sent ? `Sent to ${r.sent} -- it should arrive in a few seconds.` : "Nothing was sent.") +
                  refused.map((d) => ` ${d.device} (${d.relay}) refused: ${d.reason}`).join("")
                );
              })}>
                Send a test
              </button>
              <button className={button} disabled={busy} onClick={turnOff}>Turn off here</button>
            </>
          ) : (
            <button className={primary} disabled={busy || sub === undefined} onClick={turnOn}>
              Turn on for this device
            </button>
          )}
          {note && <span className="text-xs text-slate-500">{note}</span>}
        </div>
      )}

      {devices.length > 0 && (
        <ul className="text-xs text-slate-500 space-y-0.5">
          {devices.map((d, i) => (
            <li key={i}>
              {d.label} <span className="text-slate-600">· since {d.since.slice(0, 10)}</span>
            </li>
          ))}
        </ul>
      )}

      <p className="text-[11px] text-slate-600 leading-relaxed">
        Each notification travels as an empty wake-up through your browser's push service (Apple,
        Google or Mozilla); the phone then fetches who wrote and what from this server. The push
        service never sees a name or a word of a chat.
      </p>
    </div>
  );
}

/**
 * Two-factor sign-in (app/auth.py, TOTP). Set up in two steps -- add the key
 * to an authenticator app, then confirm with a code -- so a half-done setup
 * never locks anyone out. Recovery codes are shown exactly once.
 */
function TwoFactorSection() {
  const [status, setStatus] = useState(null);
  const [pw, setPw] = useState("");
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState(null); // {secret, uri} while confirming
  const [recovery, setRecovery] = useState(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);

  const load = () => api.authStatus().then(setStatus).catch(() => {});
  useEffect(() => {
    load();
  }, []);

  if (!status?.enabled) return null;

  async function run(fn) {
    setBusy(true);
    setNote(null);
    try {
      await fn();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(false);
    }
  }

  const input =
    "w-full bg-ink-950 border border-ink-800 rounded-lg px-3 py-1.5 text-sm outline-none focus:border-accent/60 placeholder:text-slate-600";
  const button =
    "text-sm px-3 py-1.5 rounded-lg border border-ink-700 hover:bg-ink-850 disabled:opacity-40";
  const primary =
    "text-sm px-3 py-1.5 rounded-lg bg-accent/25 border border-accent/40 hover:bg-accent/35 disabled:opacity-40";
  const grouped = (s) => s.match(/.{1,4}/g).join(" ");

  return (
    <div className="pt-3 border-t border-ink-800 space-y-3">
      <div className="text-sm">
        Two-factor sign-in
        <span className="text-xs text-slate-600 ml-2">
          {status.totp ? `on · ${status.recovery_left ?? 0} recovery codes left` : "off"}
        </span>
      </div>

      {!status.totp && !setup && !recovery && (
        <div className="text-xs text-amber-400/90 bg-amber-950/30 border border-amber-900/40 rounded-lg px-3 py-2">
          This server is reachable from the internet; with only a password, one leaked
          password is every chat. Two-factor adds a code from your phone.
        </div>
      )}

      {recovery ? (
        <div className="space-y-2">
          <p className="text-xs text-slate-400">
            Two-factor is on. Save these recovery codes somewhere safe -- each signs in once
            without your phone, and they won't be shown again.
          </p>
          <pre className="text-sm bg-ink-950 border border-ink-800 rounded-lg px-3 py-2 grid grid-cols-2 gap-1">
            {recovery.map((c) => <span key={c}>{c}</span>)}
          </pre>
          <button className={primary} onClick={() => { setRecovery(null); load(); }}>
            I've saved them
          </button>
        </div>
      ) : setup ? (
        <div className="space-y-2">
          <p className="text-xs text-slate-400">
            Add this key to an authenticator app (Aegis, Google Authenticator, 1Password…), then
            enter the code it shows.
          </p>
          <div className="text-sm font-mono bg-ink-950 border border-ink-800 rounded-lg px-3 py-2 select-all break-all">
            {grouped(setup.secret)}
          </div>
          <a href={setup.uri} className="text-xs text-accent hover:underline">
            On this phone? Open it in the authenticator app
          </a>
          <input className={input} value={code} onChange={(e) => setCode(e.target.value)}
                 placeholder="6-digit code" inputMode="numeric" autoComplete="one-time-code" />
          <div className="flex items-center gap-2">
            <button className={primary} disabled={busy || code.trim().length < 6}
                    onClick={() => run(async () => {
                      const r = await api.totpEnable(code.trim());
                      setSetup(null);
                      setCode("");
                      setRecovery(r.recovery_codes);
                    })}>
              Turn on
            </button>
            <button className={button} onClick={() => setSetup(null)}>Cancel</button>
            {note && <span className="text-xs text-slate-500">{note}</span>}
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          <input type="password" className={input} value={pw} onChange={(e) => setPw(e.target.value)}
                 placeholder="Current password" autoComplete="current-password" />
          {status.totp && (
            <input className={input} value={code} onChange={(e) => setCode(e.target.value)}
                   placeholder="Code or recovery code" autoComplete="one-time-code" />
          )}
          <div className="flex items-center gap-2">
            {status.totp ? (
              <button className={button} disabled={busy || !pw || !code.trim()}
                      onClick={() => run(async () => {
                        await api.totpDisable(pw, code.trim());
                        setPw("");
                        setCode("");
                        await load();
                        setNote("Two-factor is off.");
                      })}>
                Turn off
              </button>
            ) : (
              <button className={primary} disabled={busy || !pw}
                      onClick={() => run(async () => {
                        setSetup(await api.totpSetup(pw));
                        setPw("");
                      })}>
                Set up
              </button>
            )}
            {note && <span className="text-xs text-slate-500">{note}</span>}
          </div>
        </div>
      )}

      <p className="text-[11px] text-slate-600 leading-relaxed">
        Turning it on signs out every other device. Logins are also throttled per visitor, and a
        lockout from the internet never blocks signing in from your own network.
      </p>
    </div>
  );
}

/**
 * Test this model: a scripted 12-turn chat through the real pipeline, scored.
 *
 * The numbers are the ones every tuning decision here was taken on, so "is
 * this model working?" has an answer without reading transcripts. The chat is
 * temporary and deleted afterwards; it shares the GPU while it runs.
 */
function TestModelSection() {
  const [chars, setChars] = useState([]);
  const [personas, setPersonas] = useState([]);
  const [charId, setCharId] = useState("");
  const [personaId, setPersonaId] = useState("");
  const [progress, setProgress] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [showTurns, setShowTurns] = useState(false);
  const abortRef = useRef(null);

  useEffect(() => {
    api.characters().then((c) => {
      setChars(c);
      if (c.length) setCharId(String(c[0].id));
    }).catch(() => {});
    api.personas().then(setPersonas).catch(() => {});
    return () => abortRef.current?.();
  }, []);

  function start() {
    setResult(null);
    setError(null);
    setProgress({ turn: 0, of: 12 });
    abortRef.current = streamTurn(
      "/eval",
      { character_id: Number(charId), persona_id: personaId ? Number(personaId) : null },
      {
        onEvent: (evt) => {
          if (evt.type === "progress") setProgress({ turn: evt.turn, of: evt.of });
          if (evt.type === "result") {
            setResult(evt);
            setProgress(null);
          }
        },
        onError: (e) => {
          setError(e.message);
          setProgress(null);
        },
        onDone: () => setProgress(null),
      }
    );
  }

  const s = result?.summary;
  const rows = s
    ? [
        ["Turns that acted for you", `${s.acted_for_you} of ${s.turns}`, `${s.lines_removed} lines removed`],
        ["Saved in first person", `${s.first_person_saved} of ${s.turns}`, `${s.redo_first_person} redone`],
        ["Replies redone after the guard", s.redo_guard, `${s.empty_replies} empty`],
        ["Average reply", `${s.avg_words} words`, `${s.avg_seconds} s`],
        ["Narration / scene detail", `${Math.round(s.narration_share * 100)}%`, `${s.scene_per_100} setting words per 100`],
        ["Repetition", `${s.repeated_openings} repeated openings`, `${s.recurring_phrases} recurring phrases`],
        ["Memory", `${s.memory_folds} condenses`, ""],
      ]
    : [];

  return (
    <div className="pt-3 border-t border-ink-800 space-y-3">
      <div className="text-sm">
        Test this model
        <span className="text-xs text-slate-600 ml-2">
          a scripted 12-turn chat with your current settings, scored
        </span>
      </div>
      <div className="flex flex-wrap gap-2 items-center">
        <select
          value={charId}
          onChange={(e) => setCharId(e.target.value)}
          disabled={!!progress}
          className="bg-ink-950 border border-ink-800 rounded-lg px-2 py-1 text-sm outline-none focus:border-accent/60"
        >
          {chars.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        <select
          value={personaId}
          onChange={(e) => setPersonaId(e.target.value)}
          disabled={!!progress}
          className="bg-ink-950 border border-ink-800 rounded-lg px-2 py-1 text-sm outline-none focus:border-accent/60"
        >
          <option value="">as You</option>
          {personas.map((p) => (
            <option key={p.id} value={p.id}>as {p.name}</option>
          ))}
        </select>
        <button
          onClick={start}
          disabled={!charId || !!progress}
          className="text-sm px-3 py-1.5 rounded-lg bg-accent/25 border border-accent/40 hover:bg-accent/35 disabled:opacity-40"
        >
          {progress ? `Turn ${progress.turn}/${progress.of}…` : "Run test"}
        </button>
      </div>
      <p className="text-[11px] text-slate-600">
        Takes a few minutes and uses the GPU while it runs. The test chat is
        deleted afterwards.
      </p>
      {error && <div className="text-xs text-rose-400">{error}</div>}
      {s && (
        <div className="space-y-2">
          {s.health.length > 0 ? (
            <ul className="text-xs text-amber-400/90 bg-amber-950/30 border border-amber-900/40 rounded-lg px-3 py-2 space-y-1">
              {s.health.map((h, i) => <li key={i}>{h}</li>)}
            </ul>
          ) : (
            <div className="text-xs text-emerald-400/90">No problems flagged.</div>
          )}
          <table className="w-full text-xs">
            <tbody>
              {rows.map(([label, a, b]) => (
                <tr key={label} className="border-t border-ink-800">
                  <td className="py-1 text-slate-400">{label}</td>
                  <td className="py-1 tabular-nums">{a}</td>
                  <td className="py-1 text-slate-500 tabular-nums">{b}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <button onClick={() => setShowTurns((v) => !v)} className="text-xs text-slate-500 hover:text-slate-300">
            {showTurns ? "Hide" : "Show"} the {result.rows.length} replies
          </button>
          {showTurns && (
            <div className="max-h-64 overflow-auto scroll-thin space-y-2 text-[11px]">
              {result.rows.map((r) => (
                <div key={r.turn} className="bg-ink-950 border border-ink-800 rounded-lg p-2">
                  <div className="text-slate-500 mb-1">
                    {r.turn}. {r.user} · {r.seconds}s
                    {r.redos.length > 0 && ` · redone (${r.redos.join(", ")})`}
                    {r.guard_removed > 0 && ` · ${r.guard_removed} lines removed`}
                  </div>
                  <div className="whitespace-pre-wrap text-slate-300">{r.reply || "(no reply)"}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
