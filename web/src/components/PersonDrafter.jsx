import { useEffect, useRef, useState } from "react";
import { api, streamTurn } from "../api";
import { Button, Input, Textarea } from "./ui";

const SHOWN = [
  ["description", "About them", 6],
  ["personality", "Personality", 3],
  ["first_mes", "Opening message", 4],
];

/**
 * A new person for a world (app/cards/people.py). The draft is nobody until it's kept:
 * as a character to chat with, as an extra who lives in the world, or, from inside a
 * chat, as a guest who arrives there. `onKept(character, how)` says which.
 */
export default function PersonDrafter({ worldId, inChat = false, onKept, onCancel }) {
  const [hint, setHint] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState("");
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  useEffect(() => () => abortRef.current?.(), []);

  function run() {
    setBusy(true);
    setError(null);
    setDraft(null);
    setPreview("");
    let acc = "";
    abortRef.current = streamTurn(
      `/worlds/${worldId}/people/draft`,
      { hint },
      {
        onToken: (t) => {
          acc += t;
          setPreview(acc);
        },
        onEvent: (evt) => {
          if (evt.type === "draft") setDraft(evt);
          if (evt.type === "refused") setError(evt.message);
        },
        onDone: () => setBusy(false),
        onError: (e) => {
          setBusy(false);
          setError(e.message);
        },
      }
    );
  }

  const setField = (k, v) => setDraft((d) => ({ ...d, fields: { ...d.fields, [k]: v } }));

  async function keep(how) {
    setError(null);
    try {
      const made = await api.createCharacter({
        ...draft.fields,
        tags: draft.tags,
        world_id: worldId,
        extra: how !== "chat",
      });
      onKept?.(made, how);
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div>
      {!draft && (
        <>
          <Textarea
            rows={2}
            value={hint}
            onChange={(e) => setHint(e.target.value)}
            placeholder="Optional: a midwife who hates horses. Leave empty to be surprised."
          />
          <div className="flex flex-wrap items-center gap-2 mt-2">
            {busy ? (
              <Button onClick={() => { abortRef.current?.(); setBusy(false); }}>Stop</Button>
            ) : (
              <Button variant="primary" onClick={run}>Draft someone</Button>
            )}
            {!busy && <Button onClick={onCancel}>Cancel</Button>}
            <span className="text-[11px] text-slate-600">about 20 s · nothing is saved until you keep them</span>
          </div>
        </>
      )}

      {busy && preview && (
        <pre className="mt-3 max-h-48 overflow-y-auto scroll-thin whitespace-pre-wrap text-xs text-slate-400">
          {preview}
        </pre>
      )}
      {error && <p className="text-xs text-rose-400 mt-2">{error}</p>}

      {draft && (
        <div className="space-y-3">
          <label className="block text-xs text-slate-400">
            Name
            <Input
              className="mt-1"
              value={draft.fields.name}
              onChange={(e) => setField("name", e.target.value)}
            />
          </label>
          {draft.tie && <p className="text-[11px] text-slate-500">Knows {draft.tie}</p>}
          {draft.missing.length > 0 && (
            <p className="text-xs text-slate-500">
              Not drafted: {draft.missing.join(", ")}. Draft again, or fill it in from the editor later.
            </p>
          )}
          {SHOWN.filter(([k]) => draft.fields[k]).map(([k, label, rows]) => (
            <label key={k} className="block text-xs text-slate-400">
              {label}
              <Textarea
                className="mt-1"
                rows={rows}
                value={draft.fields[k]}
                onChange={(e) => setField(k, e.target.value)}
              />
            </label>
          ))}
          <div className="flex flex-wrap gap-2 pt-1">
            {inChat && (
              <Button variant="primary" onClick={() => keep("guest")}>Bring them in</Button>
            )}
            <Button variant={inChat ? undefined : "primary"} onClick={() => keep("chat")}>
              {inChat ? "Add to characters" : "Chat with them"}
            </Button>
            {!inChat && <Button onClick={() => keep("extra")}>Keep as an extra</Button>}
            <Button onClick={run} disabled={busy}>Draft again</Button>
            <Button onClick={onCancel}>Discard</Button>
          </div>
        </div>
      )}
    </div>
  );
}
