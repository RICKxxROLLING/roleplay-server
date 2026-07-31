import { useEffect, useState } from "react";
import { api } from "../api";

/**
 * Summarization is lossy by nature, so this panel exists to make it inspectable
 * and correctable -- being able to fix a bad fold is what makes the whole
 * approach tolerable over long chats.
 */
export default function MemoryPanel({ open, onClose, sessionId, onChanged }) {
  const [mem, setMem] = useState(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);

  const load = async () => {
    const m = await api.memory(sessionId);
    setMem(m);
    setDraft(m.summary);
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
          </div>
        )}
      </div>
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
