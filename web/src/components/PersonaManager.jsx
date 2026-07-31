import { useEffect, useState } from "react";
import { api } from "../api";
import { Modal, Field, Input, Textarea, Button, Empty } from "./ui";

const BLANK = { name: "", description: "" };

export default function PersonaManager({ open, onClose, onChanged }) {
  const [personas, setPersonas] = useState([]);
  const [editing, setEditing] = useState(null); // id, or "new", or null
  const [form, setForm] = useState(BLANK);
  const [note, setNote] = useState(null);

  const load = () => api.personas().then(setPersonas).catch((e) => setNote(e.message));

  useEffect(() => {
    if (open) {
      load();
      setEditing(null);
      setNote(null);
    }
  }, [open]);

  function startEdit(p) {
    setEditing(p.id);
    setForm({ name: p.name, description: p.description });
  }

  async function save() {
    if (!form.name.trim()) return setNote("Give the persona a name.");
    try {
      if (editing === "new") await api.createPersona(form);
      else await api.updatePersona(editing, form);
      setEditing(null);
      await load();
      onChanged?.();
      setNote(null);
    } catch (e) {
      setNote(e.message);
    }
  }

  async function remove(p) {
    if (!confirm(`Delete persona "${p.name}"? Chats using it fall back to "You".`))
      return;
    try {
      const r = await api.deletePersona(p.id);
      await load();
      onChanged?.();
      setNote(
        r.chats_detached
          ? `Deleted. ${r.chats_detached} chat(s) now have no persona.`
          : "Deleted."
      );
    } catch (e) {
      setNote(e.message);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Personas" width={560}>
      <p className="text-xs text-slate-500 mb-4">
        A persona is who <em>you</em> are in the scene. The character sees this
        description, and <code className="text-slate-400">{"{{user}}"}</code> in a card
        resolves to the name.
      </p>

      {editing !== null ? (
        <div>
          <Field label="Name">
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Riley"
              autoFocus
            />
          </Field>
          <Field label="Description" hint="who you are, how you carry yourself">
            <Textarea
              rows={5}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="A cartographer chasing a rumour across the northern roads."
            />
          </Field>
          <div className="flex gap-2 items-center">
            <Button variant="primary" onClick={save}>
              {editing === "new" ? "Create" : "Save"}
            </Button>
            <Button onClick={() => setEditing(null)}>Cancel</Button>
            {note && <span className="text-xs text-slate-500">{note}</span>}
          </div>
        </div>
      ) : (
        <div>
          <div className="space-y-2 mb-4">
            {personas.map((p) => (
              <div
                key={p.id}
                className="flex items-start gap-3 border border-ink-800 rounded-lg px-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-sm">{p.name}</div>
                  <div className="text-xs text-slate-500 line-clamp-2">
                    {p.description || <span className="text-slate-600">No description</span>}
                  </div>
                </div>
                <Button onClick={() => startEdit(p)}>Edit</Button>
                <Button variant="danger" onClick={() => remove(p)}>
                  Delete
                </Button>
              </div>
            ))}
            {personas.length === 0 && (
              <Empty>No personas yet. Without one you're just "You".</Empty>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="primary"
              onClick={() => {
                setEditing("new");
                setForm(BLANK);
              }}
            >
              New persona
            </Button>
            {note && <span className="text-xs text-slate-500">{note}</span>}
          </div>
        </div>
      )}
    </Modal>
  );
}
