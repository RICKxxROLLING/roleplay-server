import { useEffect, useState } from "react";
import { api } from "../api";
import { Modal, Field, Input, Textarea, Button, Empty } from "./ui";
import LorebookEditor from "./LorebookEditor";
import PersonDrafter from "./PersonDrafter";

const BLANK = { name: "", description: "", entries: [] };

/**
 * Shared worlds: a setting and lore entries reused by every character in it.
 *
 * Entries are edited with the card lorebook editor, and matched exactly like a
 * card's own -- keyword-triggered, under the chatting character's budget -- so
 * a world costs no context until something in the chat mentions it.
 */
export default function WorldManager({ open, onClose, onChanged, onChat, onWorldChat }) {
  const [worlds, setWorlds] = useState([]);
  const [editing, setEditing] = useState(null); // id, "new", or null
  const [form, setForm] = useState(BLANK);
  const [note, setNote] = useState(null);
  const [drafting, setDrafting] = useState(null); // world id
  const load = () => api.worlds().then(setWorlds).catch((e) => setNote(e.message));

  useEffect(() => {
    if (open) {
      load();
      setEditing(null);
      setDrafting(null);
      setNote(null);
    }
  }, [open]);

  async function kept(person, how) {
    setDrafting(null);
    await load();
    onChanged?.();
    if (how === "chat") onChat?.(person);
    else setNote(`${person.name} is an extra now. The narrator may send them by.`);
  }

  async function promote(person) {
    try {
      await api.updateCharacter(person.id, { extra: false });
      await load();
      onChanged?.();
      onChat?.(person);
    } catch (e) {
      setNote(e.message);
    }
  }

  async function removeExtra(person) {
    if (!confirm(`Delete ${person.name}?`)) return;
    try {
      await api.deleteCharacter(person.id);
      await load();
      onChanged?.();
    } catch (e) {
      setNote(e.message);
    }
  }

  function startEdit(w) {
    setEditing(w.id);
    setForm({ name: w.name, description: w.description, entries: w.entries || [] });
  }

  // LorebookEditor edits `character_book` on a card-shaped form; a world keeps
  // the same entries under `entries`.
  const loreForm = { character_book: form.entries };
  const setLore = (key, value) => {
    if (key === "character_book") setForm((f) => ({ ...f, entries: value }));
  };

  async function save() {
    if (!form.name.trim()) return setNote("Give the world a name.");
    try {
      if (editing === "new") await api.createWorld(form);
      else await api.updateWorld(editing, form);
      setEditing(null);
      await load();
      onChanged?.();
      setNote(null);
    } catch (e) {
      setNote(e.message);
    }
  }

  async function remove(w) {
    if (!confirm(`Delete world "${w.name}"? Its characters are kept and leave the world.`)) return;
    try {
      await api.deleteWorld(w.id);
      await load();
      onChanged?.();
      setNote("Deleted.");
    } catch (e) {
      setNote(e.message);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Worlds" width={640}>
      <p className="text-xs text-slate-500 mb-4">
        A world is a setting shared by several characters — Jackson, the
        Unitarian Empire. Its description is given to every character in it, and
        its lore entries join each character&apos;s own. Put a character in a
        world from the character editor.
      </p>

      {drafting !== null ? (
        <PersonDrafter worldId={drafting} onKept={kept} onCancel={() => setDrafting(null)} />
      ) : editing !== null ? (
        <div>
          <Field label="Name">
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Jackson"
              autoFocus
            />
          </Field>
          <Field label="Setting" hint="always in the prompt, so keep it to the essentials">
            <Textarea
              rows={4}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="A fortified settlement in Wyoming, walled against the infected…"
            />
          </Field>
          <LorebookEditor form={loreForm} set={setLore} showSettings={false} />
          <div className="flex items-center gap-2 mt-4">
            <Button variant="primary" onClick={save}>Save</Button>
            <Button onClick={() => setEditing(null)}>Cancel</Button>
            {note && <span className="text-xs text-slate-500">{note}</span>}
          </div>
        </div>
      ) : (
        <div>
          <div className="space-y-2 mb-4">
            {worlds.map((w) => (
              <div key={w.id} className="border border-ink-800 rounded-lg px-3 py-2.5">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <div className="flex-1 min-w-[10rem]">
                    <div className="text-sm">{w.name}</div>
                    <div className="text-xs text-slate-500 truncate">
                      {(w.entries || []).length} entries ·{" "}
                      {w.characters.some((c) => !c.extra)
                        ? w.characters.filter((c) => !c.extra).map((c) => c.name).join(", ")
                        : "no characters yet"}
                    </div>
                  </div>
                  <Button
                    onClick={() => onWorldChat?.(w)}
                    title="A chat based in this world, where people come and go"
                  >
                    World chat
                  </Button>
                  <Button onClick={() => setDrafting(w.id)}>New person</Button>
                  <Button onClick={() => startEdit(w)}>Edit</Button>
                  <Button variant="danger" onClick={() => remove(w)}>Delete</Button>
                </div>
                {w.characters.some((c) => c.extra) && (
                  <div className="mt-2 pt-2 border-t border-ink-800 space-y-1">
                    <div className="text-[11px] text-slate-500">Extras: the narrator may send them by</div>
                    {w.characters.filter((c) => c.extra).map((c) => (
                      <div key={c.id} className="flex items-center gap-3 text-xs">
                        <span className="flex-1 min-w-0 truncate">{c.name}</span>
                        <button onClick={() => promote(c)} className="text-slate-400 hover:text-slate-200">chat</button>
                        <button onClick={() => removeExtra(c)} className="text-slate-500 hover:text-rose-400">delete</button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {worlds.length === 0 && <Empty>No worlds yet.</Empty>}
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="primary"
              onClick={() => {
                setEditing("new");
                setForm(BLANK);
              }}
            >
              New world
            </Button>
            {note && <span className="text-xs text-slate-500">{note}</span>}
          </div>
        </div>
      )}
    </Modal>
  );
}
