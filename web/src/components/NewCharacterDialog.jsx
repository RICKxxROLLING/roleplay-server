import { useEffect, useState } from "react";
import { api } from "../api";
import { Modal, Field, Input, Textarea, Button } from "./ui";

/**
 * Create a character without a card file.
 *
 * Deliberately only three fields. A character needs a name to be primed with,
 * a description to be, and an opening line to start a scene -- everything else
 * is refinement, and the full editor already does refinement well. Asking for
 * all ten fields up front turns "I have an idea" into paperwork.
 */
export default function NewCharacterDialog({ open, onClose, onCreated }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [firstMes, setFirstMes] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (open) {
      setName("");
      setDescription("");
      setFirstMes("");
      setErr(null);
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
    <Modal open={open} onClose={onClose} title="New character" width={520}>
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
