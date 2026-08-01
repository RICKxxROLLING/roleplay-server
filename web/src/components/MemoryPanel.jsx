import { useEffect, useState } from "react";
import { api } from "../api";

/**
 * Summarization is lossy by nature, so this panel exists to make it inspectable
 * and correctable -- being able to fix a bad fold is what makes the whole
 * approach tolerable over long chats.
 *
 * Vector recall lives here too rather than in generation settings: the two are
 * one story from the user's side. The summary is what the character remembers
 * of the plot, recall is what it can look up verbatim, and understanding one
 * means seeing the other next to it.
 */
export default function MemoryPanel({ open, onClose, sessionId, onChanged, onOpenModels }) {
  const [mem, setMem] = useState(null);
  const [health, setHealth] = useState(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);

  const load = async () => {
    const m = await api.memory(sessionId);
    setMem(m);
    setDraft(m.summary);
    // Best-effort: a dead backend shouldn't blank the panel, it just means we
    // can't say whether the embedding model is present.
    api.health().then(setHealth).catch(() => setHealth(null));
  };

  useEffect(() => {
    if (open && sessionId) load().catch((e) => setNote(e.message));
  }, [open, sessionId]);

  if (!open) return null;

  const dirty = mem && draft !== mem.summary;
  const pct = mem
    ? Math.min(100, Math.round((mem.pending_tokens / mem.trigger_tokens) * 100))
    : 0;

  async function save() {
    setBusy(true);
    try {
      await api.saveMemory(sessionId, draft);
      await load();
      setNote("Saved.");
      onChanged?.();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function foldNow() {
    setBusy(true);
    setNote("Condensing…");
    try {
      const r = await api.forceSummarize(sessionId);
      await load();
      setNote(
        r.folded ? `Folded ${r.folded} messages.` : "Nothing eligible to fold yet."
      );
      onChanged?.();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function toggleRecall(on) {
    setMem((prev) => ({ ...prev, retrieval_enabled: on }));
    setBusy(true);
    try {
      await api.patchSettings({ retrieval_enabled: on });
      // Switching it on mid-chat leaves the backlog unembedded, so do it now
      // rather than letting recall look broken until enough turns have passed.
      if (on) await api.reindex(sessionId);
      await load();
      setNote(on ? "Vector recall on." : "Vector recall off.");
    } catch (e) {
      setNote(e.message);
      await load().catch(() => {});
    } finally {
      setBusy(false);
    }
  }

  async function reindexNow() {
    setBusy(true);
    setNote("Embedding…");
    try {
      const r = await api.reindex(sessionId);
      await load();
      setNote(r.indexed ? `Embedded ${r.indexed} messages.` : "Already up to date.");
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 grid place-items-center z-50" onClick={onClose}>
      <div
        className="bg-ink-900 border border-ink-700 rounded-xl w-[620px] max-h-[85vh] overflow-y-auto scroll-thin"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-ink-800 flex items-center justify-between">
          <h2 className="font-semibold">Memory</h2>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300">×</button>
        </div>

        {!mem ? (
          <div className="p-5 text-sm text-slate-500">Loading…</div>
        ) : (
          <div className="p-5 space-y-4">
            <div className="grid grid-cols-3 gap-3 text-center">
              <Stat label="condensed" value={mem.summarized_count} />
              <Stat label="verbatim" value={mem.pending_count} />
              <Stat label="tokens live" value={mem.pending_tokens} />
            </div>

            <div>
              <div className="flex justify-between text-xs text-slate-500 mb-1">
                <span>toward next fold</span>
                <span>
                  {mem.pending_tokens} / {mem.trigger_tokens}
                </span>
              </div>
              <div className="h-1.5 bg-ink-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent/70 transition-all"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>

            <div>
              <label className="text-sm mb-1.5 block">
                Rolling summary
                <span className="text-xs text-slate-600 ml-2">
                  injected as “Story so far”
                </span>
              </label>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={10}
                placeholder="Nothing condensed yet — this fills in once the chat outgrows the context window."
                className="w-full bg-ink-950 border border-ink-800 rounded-lg p-3 text-sm leading-relaxed outline-none focus:border-accent/60 resize-y placeholder:text-slate-600"
              />
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={save}
                disabled={!dirty || busy}
                className="text-sm px-3 py-1.5 rounded-lg bg-accent/25 border border-accent/40 hover:bg-accent/35 disabled:opacity-40"
              >
                Save edits
              </button>
              <button
                onClick={foldNow}
                disabled={busy}
                className="text-sm px-3 py-1.5 rounded-lg border border-ink-700 hover:bg-ink-850 disabled:opacity-40"
              >
                Condense now
              </button>
              {note && <span className="text-xs text-slate-500">{note}</span>}
            </div>

            <Recall
              mem={mem}
              health={health}
              busy={busy}
              onToggle={toggleRecall}
              onReindex={reindexNow}
              onOpenModels={onOpenModels}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function Recall({ mem, health, busy, onToggle, onReindex, onOpenModels }) {
  const on = mem.retrieval_enabled;
  // null means embeddings live on another host, so this list can't answer.
  const missing = health?.ok && health.embedding_model_installed === false;

  return (
    <div className="pt-4 border-t border-ink-800 space-y-3">
      <label className="flex items-center gap-2.5 text-sm cursor-pointer">
        <input
          type="checkbox"
          checked={on}
          disabled={busy}
          onChange={(e) => onToggle(e.target.checked)}
          className="accent-accent w-4 h-4"
        />
        Vector recall
        <span className="text-xs text-slate-600">
          look up exact details the summary blurred
        </span>
      </label>

      {on && (
        <>
          {missing && (
            <div className="text-xs text-amber-400/90 bg-amber-950/30 border border-amber-900/40 rounded-lg px-3 py-2">
              <span className="font-medium">{mem.embedding_model}</span> isn't
              installed, so nothing can be embedded yet.{" "}
              {onOpenModels && (
                <button onClick={onOpenModels} className="underline hover:text-amber-300">
                  Download it
                </button>
              )}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3 text-center">
            <Stat label="embedded" value={mem.indexed_count} />
            <Stat label="searchable" value={mem.searchable_count} />
          </div>

          <p className="text-xs text-slate-600 leading-relaxed">
            Only condensed turns are searchable — anything still verbatim is in
            the prompt already, so recalling it would just repeat it.
            {mem.searchable_count === 0 &&
              " Nothing has been condensed yet, so recall has nothing to search."}
          </p>

          <div className="flex items-center gap-2">
            <button
              onClick={onReindex}
              disabled={busy}
              className="text-sm px-3 py-1.5 rounded-lg border border-ink-700 hover:bg-ink-850 disabled:opacity-40"
            >
              Re-embed{mem.unindexed_count > 0 ? ` (${mem.unindexed_count})` : ""}
            </button>
            <span className="text-xs text-slate-600">
              via {mem.embedding_model}
            </span>
          </div>

          <Probe sessionId={sessionId} />
        </>
      )}
    </div>
  );
}

/**
 * Calibration tool. The prompt inspector only shows hits that already passed
 * the relevance floor, so it can't tell you what scored just below it -- and a
 * threshold chosen from filtered data is a guess. This scores arbitrary text
 * against the session's memory and shows everything, rejects included.
 */
function Probe({ sessionId }) {
  const [query, setQuery] = useState("");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function run() {
    const q = query.trim();
    if (!q || busy) return;
    setBusy(true);
    setErr(null);
    try {
      setRes(await api.probeRetrieval(sessionId, q));
    } catch (e) {
      setErr(e.message);
      setRes(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pt-3 border-t border-ink-800/60 space-y-2">
      <label className="text-sm block">
        Test recall
        <span className="text-xs text-slate-600 ml-2">
          score any question without sending it
        </span>
      </label>

      <div className="flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder="What was the innkeeper's name?"
          className="flex-1 bg-ink-950 border border-ink-800 rounded-lg px-3 py-1.5 text-sm outline-none focus:border-accent/60 placeholder:text-slate-600"
        />
        <button
          onClick={run}
          disabled={busy || !query.trim()}
          className="text-sm px-3 py-1.5 rounded-lg border border-ink-700 hover:bg-ink-850 disabled:opacity-40"
        >
          {busy ? "…" : "Score"}
        </button>
      </div>

      {err && <p className="text-xs text-rose-400">{err}</p>}

      {res?.error && (
        <p className="text-xs text-amber-400/90">Embedder failed: {res.error}</p>
      )}

      {res && !res.error && (
        <div className="space-y-1.5">
          <p className="text-xs text-slate-600">
            {res.candidates} searchable · floor {res.settings.min_score} · top-k{" "}
            {res.settings.top_k}
          </p>

          {res.results.length === 0 ? (
            <p className="text-xs text-slate-600">
              Nothing to search yet — only condensed turns are searchable.
            </p>
          ) : (
            <>
              <ul className="space-y-1 max-h-56 overflow-y-auto scroll-thin">
                {res.results.map((r) => (
                  <li
                    key={r.message_id}
                    className={`text-xs rounded-lg px-2.5 py-1.5 border ${
                      r.would_inject
                        ? "border-accent/40 bg-accent/10"
                        : "border-ink-800 bg-ink-950/60 text-slate-500"
                    }`}
                  >
                    <div className="flex items-baseline gap-2">
                      <span className="tabular-nums font-medium">
                        {r.score.toFixed(3)}
                      </span>
                      <span className="text-[10px] uppercase tracking-wider text-slate-600">
                        {r.would_inject ? "injected" : r.rejected_by}
                      </span>
                    </div>
                    <p className="mt-0.5 line-clamp-2 leading-snug">{r.content}</p>
                  </li>
                ))}
              </ul>
              <p className="text-[11px] text-slate-600 leading-relaxed">
                Ask about something that <em>never happened</em> too. If unrelated
                turns score near your real hits, the floor is too low — set it in
                the gap between them.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="bg-ink-850 border border-ink-800 rounded-lg py-2.5">
      <div className="text-lg tabular-nums">{value}</div>
      <div className="text-[11px] uppercase tracking-wider text-slate-600">{label}</div>
    </div>
  );
}
