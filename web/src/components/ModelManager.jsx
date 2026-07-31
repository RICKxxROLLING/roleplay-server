import { useEffect, useRef, useState } from "react";
import { api, streamTurn } from "../api";
import { Modal, Field, Input, Button, Empty } from "./ui";

const SUGGESTED = ["mythomax", "llama3:8b", "mistral-nemo", "qwen2.5:7b"];

function mb(bytes) {
  if (!bytes) return null;
  return `${(bytes / 1e9).toFixed(1)} GB`;
}

export default function ModelManager({ open, onClose, onChanged }) {
  const [data, setData] = useState(null);
  const [pullName, setPullName] = useState("");
  const [pull, setPull] = useState(null);
  const [note, setNote] = useState(null);
  const abortRef = useRef(null);

  const load = () => api.models().then(setData).catch((e) => setNote(e.message));

  useEffect(() => {
    if (open) {
      load();
      setPull(null);
      setNote(null);
    }
    return () => abortRef.current?.();
  }, [open]);

  if (!open) return null;

  async function activate(name) {
    try {
      await api.patchSettings({ model: name });
      await load();
      onChanged?.();
      setNote(`Now using ${name}.`);
    } catch (e) {
      setNote(e.message);
    }
  }

  function startPull() {
    const name = pullName.trim();
    if (!name) return;
    setPull({ status: "starting", pct: 0 });
    setNote(null);

    abortRef.current = streamTurn(
      "/models/pull",
      { name },
      {
        onEvent: (evt) => {
          if (evt.type !== "progress") return;
          const pct =
            evt.total && evt.completed
              ? Math.round((evt.completed / evt.total) * 100)
              : 0;
          setPull({
            status: evt.status || "downloading",
            completed: evt.completed,
            total: evt.total,
            pct,
          });
        },
        onDone: async () => {
          setPull(null);
          setPullName("");
          await load();
          setNote(`Downloaded ${name}.`);
          abortRef.current = null;
        },
        onError: (e) => {
          setPull(null);
          setNote(e.message);
        },
      }
    );
  }

  const pulling = pull !== null;

  return (
    <Modal open={open} onClose={onClose} title="Models" width={560}>
      {!data ? (
        <p className="text-sm text-slate-600">Loading…</p>
      ) : (
        <div>
          {data.error && (
            <div className="text-sm text-rose-400 bg-rose-950/30 border border-rose-900/50 rounded-lg px-3 py-2 mb-4">
              Can't reach the backend: {data.error}
            </div>
          )}

          <Field label="Installed">
            <div className="space-y-2">
              {data.models.map((m) => {
                const active = m === data.active || m.split(":")[0] === data.active;
                return (
                  <div
                    key={m}
                    className={`flex items-center gap-3 border rounded-lg px-3 py-2.5 ${
                      active ? "border-accent/50 bg-accent/10" : "border-ink-800"
                    }`}
                  >
                    <span className="text-sm flex-1 truncate">{m}</span>
                    {active ? (
                      <span className="text-xs text-accent">active</span>
                    ) : (
                      <Button onClick={() => activate(m)}>Use</Button>
                    )}
                  </div>
                );
              })}
              {data.models.length === 0 && !data.error && (
                <Empty>No models installed. Download one below.</Empty>
              )}
            </div>
          </Field>

          {data.supports_pull ? (
            <Field label="Download a model" hint="any Ollama tag">
              <div className="flex gap-2">
                <Input
                  value={pullName}
                  onChange={(e) => setPullName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !pulling && startPull()}
                  placeholder="mythomax"
                  disabled={pulling}
                />
                <Button variant="primary" onClick={startPull} disabled={pulling}>
                  {pulling ? "Downloading…" : "Download"}
                </Button>
              </div>

              <div className="flex flex-wrap gap-1.5 mt-2">
                {SUGGESTED.filter((s) => !data.models.some((m) => m.startsWith(s))).map(
                  (s) => (
                    <button
                      key={s}
                      onClick={() => setPullName(s)}
                      disabled={pulling}
                      className="text-[11px] px-2 py-1 rounded-full border border-ink-800 text-slate-500 hover:text-slate-300 hover:border-ink-700 disabled:opacity-40"
                    >
                      {s}
                    </button>
                  )
                )}
              </div>

              {pull && (
                <div className="mt-3">
                  <div className="flex justify-between text-xs text-slate-500 mb-1">
                    <span>{pull.status}</span>
                    <span>
                      {pull.total
                        ? `${mb(pull.completed)} / ${mb(pull.total)}`
                        : "working…"}
                    </span>
                  </div>
                  <div className="h-1.5 bg-ink-800 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-accent/70 transition-all"
                      style={{ width: `${pull.pct}%` }}
                    />
                  </div>
                  <Button
                    className="mt-2"
                    onClick={() => {
                      abortRef.current?.();
                      setPull(null);
                      setNote("Download stopped. Partial data is kept and resumes next time.");
                    }}
                  >
                    Stop
                  </Button>
                </div>
              )}
            </Field>
          ) : (
            <p className="text-xs text-slate-500">
              This backend can't download models. Point it at weights already on disk.
            </p>
          )}

          {note && <p className="text-xs text-slate-500 mt-2">{note}</p>}
        </div>
      )}
    </Modal>
  );
}
