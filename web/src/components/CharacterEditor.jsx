import { useEffect, useState } from "react";
import { api } from "../api";
import { Modal, Field, Input, Textarea, Button } from "./ui";
import LorebookEditor from "./LorebookEditor";
import CardDrafter from "./CardDrafter";
import { isPlaying, onPlayerChange, play, stopPlaying } from "../player";

const FIELDS = [
  ["description", "Description", 5, "Who they are, appearance, history."],
  ["personality", "Personality", 3, "Traits and manner."],
  ["scenario", "Scenario", 3, "The situation the scene opens in."],
  ["first_mes", "Opening message", 4, "How the character starts the scene."],
  ["mes_example", "Example dialogue", 4, "Few-shot samples of their voice."],
  ["system_prompt", "System prompt override", 3, "Replaces the default RP directive."],
  ["post_history_instructions", "Post-history instructions", 3, "Injected just before the reply."],
];

const DAY = [
  ["wake", "Wakes"],
  ["sleep", "Sleeps"],
  ["busy_from", "Busy from"],
  ["busy_until", "Busy until"],
];

/**
 * Their own day, for chats where they write first (app/chat/rhythm.py). Saved on its
 * own button, since it is not card text. Leave a pair empty and that part doesn't apply.
 */
function DailyRhythm({ characterId, initial }) {
  const [day, setDay] = useState(initial);
  const [note, setNote] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDay(initial), [initial]);
  const set = (k, v) => setDay((d) => ({ ...d, [k]: v }));

  async function suggest() {
    setBusy(true);
    setNote(null);
    try {
      const { reason, ...got } = await api.suggestRhythm(characterId);
      setDay(got);
      setNote(reason ? `${reason} Not saved yet.` : "Not saved yet.");
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    try {
      const body = { wake: null, sleep: null, busy_from: null, busy_until: null, reach: null };
      for (const [k, v] of Object.entries(day)) if (v) body[k] = v;
      const r = await api.updateCharacter(characterId, { rhythm: body });
      setDay(r.rhythm || {});
      setNote("Saved.");
    } catch (e) {
      setNote(e.message);
    }
  }

  return (
    <Field label="Daily rhythm" hint="when they write first; your quiet hours still win">
      <div className="grid grid-cols-2 gap-2">
        {DAY.map(([k, label]) => (
          <label key={k} className="text-xs text-slate-400">
            {label}
            <Input
              type="time"
              className="mt-1"
              value={day[k] || ""}
              onChange={(e) => set(k, e.target.value)}
            />
          </label>
        ))}
      </div>
      <label className="block text-xs text-slate-400 mt-2">
        Reaches out
        <select
          value={day.reach || ""}
          onChange={(e) => set("reach", e.target.value)}
          className="mt-1 w-full bg-ink-950 border border-ink-800 rounded-lg px-3 py-2 text-sm outline-none focus:border-accent/60"
        >
          <option value="">Usual</option>
          <option value="often">Often</option>
          <option value="sometimes">Sometimes</option>
          <option value="rarely">Rarely</option>
        </select>
      </label>
      <div className="flex flex-wrap items-center gap-2 mt-2">
        <Button onClick={suggest} disabled={busy}>{busy ? "Thinking…" : "Suggest from the card"}</Button>
        <Button onClick={save} disabled={busy}>Save rhythm</Button>
        {note && <span className="text-xs text-slate-500">{note}</span>}
      </div>
    </Field>
  );
}

/** Who reads their lines. Saved as soon as it's chosen, since it isn't card text. */
function VoicePicker({ characterId, voice, effective }) {
  const [list, setList] = useState([]);
  const [chosen, setChosen] = useState(voice || "");
  const [shown, setShown] = useState(effective);
  const [note, setNote] = useState(null);
  const [, redraw] = useState(0);
  useEffect(() => {
    api.voices().then((r) => setList(r.voices)).catch(() => {});
    return onPlayerChange(() => redraw((n) => n + 1));
  }, []);
  useEffect(() => {
    setChosen(voice || "");
    setShown(effective);
  }, [voice, effective]);

  async function choose(value) {
    setChosen(value);
    setNote(null);
    try {
      await api.updateCharacter(characterId, { voice: value });
      const c = await api.character(characterId);
      setShown(c.effective_voice);
    } catch (e) {
      setNote(e.message);
    }
  }

  const previewPath = shown ? `/voices/${shown}/preview` : null;
  const name = (v) => `${v.label}: ${v.gender}, ${v.accent}`;
  const fallback = list.find((v) => v.id === shown);
  return (
    <Field label="Voice" hint="for reading aloud; a voice downloads about 63 MB the first time it's used">
      <div className="flex gap-2">
        <select
          value={chosen}
          onChange={(e) => choose(e.target.value)}
          className="flex-1 min-w-0 bg-ink-950 border border-ink-800 rounded-lg px-3 py-2 text-sm outline-none focus:border-accent/60"
        >
          <option value="">Automatic{!chosen && fallback ? ` (${fallback.label})` : ""}</option>
          {list.map((v) => (
            <option key={v.id} value={v.id}>{name(v)}</option>
          ))}
        </select>
        <Button
          disabled={!previewPath}
          onClick={() => (isPlaying(previewPath) ? stopPlaying() : play(previewPath, setNote))}
        >
          {isPlaying(previewPath) ? "Stop" : "Listen"}
        </Button>
      </div>
      {note && <p className="text-xs text-rose-400 mt-1">{note}</p>}
    </Field>
  );
}

export default function CharacterEditor({ open, onClose, characterId, onChanged }) {
  const [card, setCard] = useState(null);
  const [form, setForm] = useState({});
  const [note, setNote] = useState(null);
  const [busy, setBusy] = useState(false);
  const [worlds, setWorlds] = useState([]);
  const [worldId, setWorldId] = useState(null);
  const [day, setDay] = useState({});
  const [voice, setVoice] = useState({ voice: null, effective: null });

  useEffect(() => {
    if (!open || !characterId) return;
    setNote(null);
    api
      .character(characterId)
      .then((c) => {
        setCard(c.card);
        setForm(c.card);
        setWorldId(c.world_id ?? null);
        setDay(c.rhythm || {});
        setVoice({ voice: c.voice, effective: c.effective_voice });
      })
      .catch((e) => setNote(e.message));
    api.worlds().then(setWorlds).catch(() => {});
  }, [open, characterId]);

  // Saved at once rather than with the card: membership is not card text, and
  // tying it to the Save button would lose it on a cancelled edit.
  async function chooseWorld(value) {
    const id = value === "" ? null : Number(value);
    try {
      await api.setWorld(characterId, id);
      setWorldId(id);
      setNote(id ? "Added to the world." : "Removed from the world.");
      onChanged?.();
    } catch (e) {
      setNote(e.message);
    }
  }

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
          {/* A thin card is the main cause of flat replies; this drafts the
              missing depth from what is already written. */}
          <CardDrafter
            characterId={characterId}
            current={form}
            onApply={(fields) => setForm({ ...form, ...fields })}
          />

          <Field label="Name">
            <Input value={form.name || ""} onChange={(e) => set("name", e.target.value)} />
          </Field>

          <Field label="Tags" hint="comma separated; a romance tag lets earned trust unlock romance for an adult character">
            <Input
              value={(form.tags || []).join(", ")}
              onChange={(e) => set("tags", e.target.value.split(",").map((t) => t.trimStart()))}
              onBlur={() => set("tags", (form.tags || []).map((t) => t.trim()).filter(Boolean))}
            />
          </Field>

          <Field label="World" hint="shared setting and lore; manage them under Worlds">
            <select
              value={worldId ?? ""}
              onChange={(e) => chooseWorld(e.target.value)}
              className="w-full bg-ink-950 border border-ink-800 rounded-lg px-3 py-2 text-sm outline-none focus:border-accent/60"
            >
              <option value="">No world</option>
              {worlds.map((w) => (
                <option key={w.id} value={w.id}>{w.name}</option>
              ))}
            </select>
          </Field>

          <VoicePicker characterId={characterId} voice={voice.voice} effective={voice.effective} />

          <DailyRhythm characterId={characterId} initial={day} />

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
