import { useEffect, useState } from "react";
import { api } from "../api";
import { Modal, Field, Input, Textarea, Button } from "./ui";
import CardDrafter from "./CardDrafter";

// Filled only by a draft; shown once they have something in them.
const EXTRA = [
  ["personality", "Personality", 3],
  ["scenario", "Scenario", 2],
  ["mes_example", "Example dialogue", 5],
];

/**
 * Create a character without a card file.
 *
 * Deliberately only three fields. A character needs a name to be primed with,
 * a description to be, and an opening line to start a scene -- everything else
 * is refinement, and the full editor already does refinement well. Asking for
 * all ten fields up front turns "I have an idea" into paperwork.
 *
 * Or describe the idea and let the model draft the card; its extra fields
 * (personality, scenario, examples) appear only once a draft fills them.
 */
export default function NewCharacterDialog({ open, onClose, onCreated }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [firstMes, setFirstMes] = useState("");
  const [extra, setExtra] = useState({});
  const [worlds, setWorlds] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (open) {
      setName("");
      setDescription("");
      setFirstMes("");
      setExtra({});
      setErr(null);
      api.worlds().then(setWorlds).catch(() => {});
    }
  }, [open]);

  async function create() {
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const c = await api.createCharacter({
        name: trimmed,
        description: description.trim(),
        first_mes: firstMes.trim(),
        ...Object.fromEntries(Object.entries(extra).map(([k, v]) => [k, v.trim()])),
      });
      onCreated?.(c.id);
      onClose?.();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="New character" width={560}>
      <CardDrafter
        worlds={worlds}
        onApply={(f) => {
          if (f.name !== undefined) setName(f.name);
          if (f.description !== undefined) setDescription(f.description);
          if (f.first_mes !== undefined) setFirstMes(f.first_mes);
          const rest = Object.fromEntries(EXTRA.filter(([k]) => f[k] !== undefined).map(([k]) => [k, f[k]]));
          setExtra((e) => ({ ...e, ...rest }));
        }}
      />

      <Field label="Name" hint="required — the model is primed to speak as this">
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && create()}
          placeholder="Bram Halloway"
          autoFocus
        />
      </Field>

      <Field label="Description" hint="who they are, appearance, history">
        <Textarea
          rows={4}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="{{char}} keeps a brass ledger behind the bar of the Salt Lantern…"
        />
      </Field>

      <Field label="Opening message" hint="how they start the scene">
        <Textarea
          rows={3}
          value={firstMes}
          onChange={(e) => setFirstMes(e.target.value)}
          placeholder="He looks up from the ledger. &quot;You're late, {{user}}.&quot;"
        />
      </Field>

      {EXTRA.filter(([k]) => extra[k] !== undefined).map(([k, label, rows]) => (
        <Field key={k} label={label}>
          <Textarea rows={rows} value={extra[k]} onChange={(e) => setExtra({ ...extra, [k]: e.target.value })} />
        </Field>
      ))}

      <p className="text-xs text-slate-600 -mt-2 mb-3">
        <code className="text-slate-500">{"{{char}}"}</code> and{" "}
        <code className="text-slate-500">{"{{user}}"}</code> are substituted with
        the character and persona names. You can fill in personality, scenario,
        example dialogue and a lorebook afterwards from <em>edit</em>.
      </p>

      {err && <p className="text-xs text-rose-400 mb-2">{err}</p>}

      <div className="flex items-center gap-2">
        <Button variant="primary" onClick={create} disabled={busy || !name.trim()}>
          {busy ? "Creating…" : "Create"}
        </Button>
        <Button onClick={onClose}>Cancel</Button>
      </div>
    </Modal>
  );
}
