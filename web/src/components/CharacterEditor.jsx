import { useEffect, useState } from "react";
import { api } from "../api";
import { Modal, Field, Input, Textarea, Button } from "./ui";
import LorebookEditor from "./LorebookEditor";

const FIELDS = [
  ["description", "Description", 5, "Who they are, appearance, history."],
  ["personality", "Personality", 3, "Traits and manner."],
  ["scenario", "Scenario", 3, "The situation the scene opens in."],
  ["first_mes", "Opening message", 4, "How the character starts the scene."],
  ["mes_example", "Example dialogue", 4, "Few-shot samples of their voice."],
  ["system_prompt", "System prompt override", 3, "Replaces the default RP directive."],
  ["post_history_instructions", "Post-history instructions", 3, "Injected just before the reply."],
];

export default function CharacterEditor({ open, onClose, characterId, onChanged }) {
  const [card, setCard] = useState(null);
  const [form, setForm] = useState({});
  const [note, setNote] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open || !characterId) return;
    setNote(null);
    api
      .character(characterId)
      .then((c) => {
        setCard(c.card);
        setForm(c.card);
      })
      .catch((e) => setNote(e.message));
  }, [open, characterId]);

  if (!open) return null;

  const dirty = card && JSON.stringify(form) !== JSON.stringify(card);
  const set = (k, v) => setForm({ ...form, [k]: v });

  async function save() {
    setBusy(true);
    try {
      const patch = {
        name: form.name,
        alternate_greetings: form.alternate_greetings,
        tags: form.tags,
        character_book: form.character_book,
        lorebook_scan_depth: form.lorebook_scan_depth,
        lorebook_token_budget: form.lorebook_token_budget,
        lorebook_recursive: form.lorebook_recursive,
      };
      for (const [k] of FIELDS) patch[k] = form[k] ?? "";
      const r = await api.updateCharacter(characterId, patch);
      setCard(r.card);
      setForm(r.card);
      setNote("Saved.");
      onChanged?.();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (
      !confirm(
        `Delete "${card?.name}"? This also deletes every chat with them. Cannot be undone.`
      )
    )
      return;
    try {
      await api.deleteCharacter(characterId);
      onChanged?.();
      onClose();
    } catch (e) {
      setNote(e.message);
    }
  }

  function setGreeting(i, value) {
    const next = [...(form.alternate_greetings || [])];
    next[i] = value;
    set("alternate_greetings", next);
  }

  return (
    <Modal open={open} onClose={onClose} title="Edit character" width={640}>
      {!card ? (
        <p className="text-sm text-slate-600">Loading…</p>
      ) : (
        <div>
          <Field label="Name">
            <Input value={form.name || ""} onChange={(e) => set("name", e.target.value)} />
          </Field>

          {FIELDS.map(([key, label, rows, hint]) => (
            <Field key={key} label={label} hint={hint}>
              <Textarea
                rows={rows}
                value={form[key] || ""}
                onChange={(e) => set(key, e.target.value)}
              />
            </Field>
          ))}

          <Field
            label="Alternate greetings"
            hint="offered when starting a new chat"
          >
            <div className="space-y-2">
              {(form.alternate_greetings || []).map((g, i) => (
                <div key={i} className="flex gap-2 items-start">
                  <Textarea
                    rows={2}
                    value={g}
                    onChange={(e) => setGreeting(i, e.target.value)}
                  />
                  <Button
                    variant="danger"
                    onClick={() =>
                      set(
                        "alternate_greetings",
                        form.alternate_greetings.filter((_, x) => x !== i)
                      )
                    }
                  >
                    ×
                  </Button>
                </div>
              ))}
              <Button
                onClick={() =>
                  set("alternate_greetings", [...(form.alternate_greetings || []), ""])
                }
              >
                Add greeting
              </Button>
            </div>
          </Field>

          <LorebookEditor form={form} set={set} />

          <div className="flex items-center gap-2 pt-2 border-t border-ink-800 mt-4">
            <Button variant="primary" onClick={save} disabled={!dirty || busy}>
              Save changes
            </Button>
            <Button variant="danger" onClick={remove}>
              Delete character
            </Button>
            {note && <span className="text-xs text-slate-500">{note}</span>}
          </div>
        </div>
      )}
    </Modal>
  );
}
