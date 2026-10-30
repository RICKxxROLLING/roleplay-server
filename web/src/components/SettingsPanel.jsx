import { useEffect, useRef, useState } from "react";
import { api, streamTurn } from "../api";
import { Textarea } from "./ui";

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

export default function SettingsPanel({ open, onClose, sessionId, onOpenModels }) {
  const [s, setS] = useState(null);
  const [prompt, setPrompt] = useState(null);

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

  return (
    <div className="fixed inset-0 bg-black/60 grid place-items-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-ink-900 border border-ink-700 rounded-xl w-full max-w-[560px] max-h-[85vh] overflow-y-auto scroll-thin"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-ink-800 flex items-center justify-between">
          <h2 className="font-semibold">Generation settings</h2>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300">×</button>
        </div>

        <div className="p-5 space-y-5">
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span>
              Backend <span className="text-slate-300">{s.backend}</span> · model{" "}
              <span className="text-slate-300">{s.model}</span> · context{" "}
              <span className="text-slate-300">{s.context_tokens}</span> tokens
            </span>
            <button
              onClick={onOpenModels}
              className="ml-auto shrink-0 border border-ink-700 rounded-lg px-2 py-1 hover:bg-ink-850"
            >
              Change model
            </button>
          </div>
          <p className="text-xs text-slate-600 -mt-2">
            Changes save automatically and persist across restarts. The prompt
            format, context and samplers are saved per model and come back when
            you switch to it.
          </p>

          <div>
            <div className="flex justify-between items-center text-sm mb-1">
              <label htmlFor="prompt-format">Prompt format</label>
              <select
                id="prompt-format"
                value={s.prompt_format}
                onChange={(e) => update("prompt_format", e.target.value)}
                className="bg-ink-950 border border-ink-800 rounded-lg px-2 py-1 text-sm outline-none focus:border-accent/60"
              >
                {PROMPT_FORMATS.map((f) => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </select>
            </div>
            <p className="text-xs text-slate-600 mt-0.5">
              Must match how the model was trained — check its model card. The wrong
              one still answers, but thinner, and drifts as a chat gets long.
            </p>
          </div>

          <div>
            <label htmlFor="narration-style" className="text-sm block mb-1">
              Narration style
            </label>
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
              Added to every character's prompt after its own instructions. Use
              {" {{char}}"} and {"{{user}}"} for names. Leave blank to add nothing.
            </p>
          </div>

          <label className="flex items-start gap-2.5 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={s.guard_user_actions}
              onChange={(e) => update("guard_user_actions", e.target.checked)}
              className="accent-accent w-4 h-4 mt-0.5"
            />
            <span>
              Keep the character from acting for you
              <span className="block text-xs text-slate-600">
                Removes lines where the reply narrates your actions or speaks for
                you, before it is saved. One such line left in a chat teaches the
                model to keep doing it.
              </span>
            </span>
          </label>

          <label className="flex items-start gap-2.5 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={s.redo_first_person}
              onChange={(e) => update("redo_first_person", e.target.checked)}
              className="accent-accent w-4 h-4 mt-0.5"
            />
            <span>
              Redo replies that slip into first person
              <span className="block text-xs text-slate-600">
                When a third-person character starts narrating as "I", the reply is
                written again once. Costs one extra generation when it happens.
              </span>
            </span>
          </label>

          {SLIDERS.map(({ key, min, max, step, hint }) => (
            <div key={key}>
              <div className="flex justify-between text-sm mb-1">
                <label className="capitalize">{key.replace(/_/g, " ")}</label>
                <span className="text-accent tabular-nums">{s[key]}</span>
              </div>
              <input
                type="range"
                min={min}
                max={max}
                step={step}
                value={s[key]}
                onChange={(e) => update(key, Number(e.target.value))}
                className="w-full accent-accent"
              />
              <p className="text-xs text-slate-600 mt-0.5">{hint}</p>
            </div>
          ))}

          <div className="pt-3 border-t border-ink-800 space-y-4">
            {/* Independent of summarization, so it sits outside that block. */}
            <label className="flex items-center gap-2.5 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={s.scene_enabled}
                onChange={(e) => update("scene_enabled", e.target.checked)}
                className="accent-accent w-4 h-4"
              />
              Track the scene
              <span className="text-xs text-slate-600">
                where, when, weather and who is present · under a second per reply
              </span>
            </label>

            <label className="flex items-center gap-2.5 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={s.thoughts_enabled}
                onChange={(e) => update("thoughts_enabled", e.target.checked)}
                className="accent-accent w-4 h-4"
              />
              Their own thoughts
              <span className="text-xs text-slate-600">
                a private journal and opinion of you, after each reply · about 2 s
              </span>
            </label>

            <AutonomySettings s={s} update={update} />

            <label className="flex items-center gap-2.5 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={s.summary_enabled}
                onChange={(e) => update("summary_enabled", e.target.checked)}
                className="accent-accent w-4 h-4"
              />
              Rolling summarization
              <span className="text-xs text-slate-600">
                condense old turns to survive the context window
              </span>
            </label>

            {s.summary_enabled && (
              <>
                <label className="flex items-center gap-2.5 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={s.facts_enabled}
                    onChange={(e) => update("facts_enabled", e.target.checked)}
                    className="accent-accent w-4 h-4"
                  />
                  Keep a fact list
                  <span className="text-xs text-slate-600">
                    names, objects and promises beside the summary · one extra
                    generation per condense
                  </span>
                </label>
                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <label>Fold threshold</label>
                    <span className="text-accent tabular-nums">
                      {s.summary_trigger_tokens} tokens
                    </span>
                  </div>
                  <input
                    type="range"
                    min={400}
                    max={3000}
                    step={100}
                    value={s.summary_trigger_tokens}
                    onChange={(e) =>
                      update("summary_trigger_tokens", Number(e.target.value))
                    }
                    className="w-full accent-accent"
                  />
                  <p className="text-xs text-slate-600 mt-0.5">
                    Condense once un-summarized history passes this size
                  </p>
                </div>

                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <label>Keep verbatim</label>
                    <span className="text-accent tabular-nums">
                      {s.keep_recent_messages} messages
                    </span>
                  </div>
                  <input
                    type="range"
                    min={2}
                    max={30}
                    step={1}
                    value={s.keep_recent_messages}
                    onChange={(e) =>
                      update("keep_recent_messages", Number(e.target.value))
                    }
                    className="w-full accent-accent"
                  />
                  <p className="text-xs text-slate-600 mt-0.5">
                    Recent turns never get condensed
                  </p>
                </div>
              </>
            )}
          </div>

          {/* Tuning only. The on/off switch lives in the Memory panel, next to
              the summary it works alongside. */}
          {s.retrieval_enabled && (
            <div className="pt-3 border-t border-ink-800 space-y-4">
              <div className="text-sm">
                Vector recall
                <span className="text-xs text-slate-600 ml-2">
                  tuning · toggle in the Memory panel
                </span>
              </div>

              <div>
                <div className="flex justify-between text-sm mb-1">
                  <label>Moments recalled</label>
                  <span className="text-accent tabular-nums">{s.retrieval_top_k}</span>
                </div>
                <input
                  type="range"
                  min={1}
                  max={10}
                  step={1}
                  value={s.retrieval_top_k}
                  onChange={(e) => update("retrieval_top_k", Number(e.target.value))}
                  className="w-full accent-accent"
                />
                <p className="text-xs text-slate-600 mt-0.5">
                  Most past turns re-injected per reply
                </p>
              </div>

              <div>
                <div className="flex justify-between text-sm mb-1">
                  <label>Relevance floor</label>
                  <span className="text-accent tabular-nums">
                    {s.retrieval_min_score}
                  </span>
                </div>
                <input
                  type="range"
                  min={0.1}
                  max={0.9}
                  step={0.05}
                  value={s.retrieval_min_score}
                  onChange={(e) =>
                    update("retrieval_min_score", Number(e.target.value))
                  }
                  className="w-full accent-accent"
                />
                <p className="text-xs text-slate-600 mt-0.5">
                  Raise it if the character brings up unrelated old scenes; lower
                  it if it fails to remember things it should
                </p>
              </div>

              <div>
                <div className="flex justify-between text-sm mb-1">
                  <label>Recall budget</label>
                  <span className="text-accent tabular-nums">
                    {s.retrieval_budget_tokens} tokens
                  </span>
                </div>
                <input
                  type="range"
                  min={100}
                  max={1200}
                  step={50}
                  value={s.retrieval_budget_tokens}
                  onChange={(e) =>
                    update("retrieval_budget_tokens", Number(e.target.value))
                  }
                  className="w-full accent-accent"
                />
                <p className="text-xs text-slate-600 mt-0.5">
                  Context spent on recall, taken from what history could use
                </p>
              </div>
            </div>
          )}

          <TestModelSection />

          <PasswordSection />
          <TwoFactorSection />
          <NotificationsSection />

          {sessionId && (
            <div className="pt-2 border-t border-ink-800">
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
                    {" · "}{prompt.dropped_messages} messages
                    trimmed · {prompt.lore_entries ?? 0} lore entries
                    {prompt.lore_dropped > 0 &&
                      ` (${prompt.lore_dropped} over budget)`}
                    {" · "}
                    {prompt.pinned_entries ?? 0} pinned
                    {prompt.pinned_dropped > 0 && ` (${prompt.pinned_dropped} over budget)`}
                    {" · "}
                    {prompt.retrieved_entries ?? 0} recalled
                    {prompt.retrieval_dropped > 0 &&
                      ` (${prompt.retrieval_dropped} over budget)`}
                  </div>
                  {prompt.retrieval_error && (
                    <div className="text-xs text-amber-400/90 mb-1">
                      Vector recall unavailable: {prompt.retrieval_error}
                    </div>
                  )}
                  {prompt.retrieved?.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-2">
                      {prompt.retrieved.map((h) => (
                        <span
                          key={h.message_id}
                          title={h.content}
                          className="text-[10px] px-2 py-0.5 rounded-full bg-sky-500/15 border border-sky-500/25 text-sky-300/90"
                        >
                          #{h.message_id} · {h.score.toFixed(2)}
                        </span>
                      ))}
                    </div>
                  )}
                  {prompt.lore_fired?.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-2">
                      {prompt.lore_fired.map((f, i) => (
                        <span
                          key={i}
                          className="text-[10px] px-2 py-0.5 rounded-full bg-accent/15 border border-accent/25 text-accent/90"
                        >
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
          )}
        </div>
      </div>
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
