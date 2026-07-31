import { useEffect, useState } from "react";
import { api } from "../api";

const SLIDERS = [
  { key: "temperature", min: 0.1, max: 2, step: 0.05, hint: "Higher = more creative, less coherent" },
  { key: "top_p", min: 0.1, max: 1, step: 0.05, hint: "Nucleus sampling cutoff" },
  { key: "top_k", min: 0, max: 200, step: 1, hint: "0 disables top-k" },
  { key: "repeat_penalty", min: 1, max: 1.5, step: 0.01, hint: "Curbs looping; >1.2 can flatten voice" },
  { key: "max_new_tokens", min: 64, max: 1024, step: 16, hint: "Reply length cap" },
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
    <div className="fixed inset-0 bg-black/60 grid place-items-center z-50" onClick={onClose}>
      <div
        className="bg-ink-900 border border-ink-700 rounded-xl w-[560px] max-h-[85vh] overflow-y-auto scroll-thin"
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
            Changes save automatically and persist across restarts.
          </p>

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
                    ~{prompt.estimated_tokens} tokens · {prompt.dropped_messages} messages
                    trimmed · {prompt.lore_entries ?? 0} lore entries
                    {prompt.lore_dropped > 0 &&
                      ` (${prompt.lore_dropped} over budget)`}
                  </div>
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
