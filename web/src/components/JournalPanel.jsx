import { useEffect, useState } from "react";
import { api } from "../api";
import { Modal, Button, Empty } from "./ui";

/**
 * Each character's private journal in this chat (app/memory/thoughts.py).
 *
 * Written after every reply and never shown mid-chat -- this panel is the only
 * place it appears. The "view" on top is the one line later prompts read; the
 * entries are the character's own words and stay out of the prompt.
 */
export default function JournalPanel({ open, onClose, sessionId }) {
  const [data, setData] = useState(null);
  const [tab, setTab] = useState(0);
  const [note, setNote] = useState(null);

  const load = () =>
    api.journal(sessionId).then(setData).catch((e) => setNote(e.message));

  useEffect(() => {
    if (open && sessionId) {
      setData(null);
      setTab(0);
      setNote(null);
      load();
    }
  }, [open, sessionId]);

  const who = data?.[tab];

  async function forget() {
    if (!confirm(`Forget everything ${who.name} privately thinks in this chat? They start forming an opinion afresh.`)) return;
    try {
      await api.forgetJournal(sessionId, who.character_id);
      await load();
      setNote(`${who.name} starts afresh from the next reply.`);
    } catch (e) {
      setNote(e.message);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Journal" width={600}>
      {!data ? (
        <p className="text-sm text-slate-600">Loading…</p>
      ) : (
        <div>
          {data.length > 1 && (
            <div className="flex gap-1.5 mb-4">
              {data.map((c, i) => (
                <button
                  key={c.character_id}
                  onClick={() => setTab(i)}
                  className={`px-2.5 py-1 rounded-full text-xs border ${
                    tab === i ? "border-accent/50 bg-accent/10 text-slate-200" : "border-ink-800 text-slate-400 hover:border-ink-700"
                  }`}
                >
                  {c.name}
                </button>
              ))}
            </div>
          )}

          {who && (
            <>
              <div className="text-xs uppercase tracking-wider text-slate-600 mb-1">
                What {who.name} thinks of you now
              </div>
              <p className="text-sm text-slate-300 mb-4 leading-relaxed">
                {who.view || <span className="text-slate-600">Nothing yet -- it forms after their next reply.</span>}
              </p>

              <div className="space-y-3 max-h-[50vh] overflow-y-auto scroll-thin pr-1">
                {who.entries.map((e) => (
                  <div key={e.id} className="border-l-2 border-ink-700 pl-3">
                    <div className="text-[11px] text-slate-600 mb-0.5">
                      {e.created_at ? new Date(e.created_at + (e.created_at.endsWith("Z") ? "" : "Z")).toLocaleString() : ""}
                    </div>
                    <p className="text-sm text-slate-400 italic leading-relaxed">{e.journal}</p>
                  </div>
                ))}
                {who.entries.length === 0 && <Empty>No entries yet.</Empty>}
              </div>

              <div className="flex items-center gap-2 mt-4">
                <Button onClick={forget} disabled={!who.entries.length}>Forget</Button>
                {note && <span className="text-xs text-slate-500">{note}</span>}
              </div>
            </>
          )}

          <p className="text-[11px] text-slate-600 leading-relaxed mt-4">
            After each reply the character jots down what just happened and how they feel about you. Only
            the one-line view above goes back into their prompts; the journal is theirs.
          </p>
        </div>
      )}
    </Modal>
  );
}
