import { useEffect, useState } from "react";
import { api } from "../api";
import { Modal, Field, Input, Button } from "./ui";

/** Substitute placeholders so the greeting preview reads the way it will in-scene. */
function preview(text, charName, userName) {
  return (text || "")
    .replaceAll("{{char}}", charName || "Character")
    .replaceAll("{{user}}", userName || "You")
    .replaceAll("<BOT>", charName || "Character")
    .replaceAll("<USER>", userName || "You");
}

export default function NewChatDialog({
  open,
  onClose,
  character,
  personas,
  onCreated,
  onManagePersonas,
}) {
  const [card, setCard] = useState(null);
  const [personaId, setPersonaId] = useState("");
  const [greetingIndex, setGreetingIndex] = useState(0);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open || !character) return;
    setError(null);
    setGreetingIndex(0);
    setPersonaId(personas[0]?.id ?? "");
    setTitle(`Chat with ${character.name}`);
    api
      .character(character.id)
      .then((c) => setCard(c.card))
      .catch((e) => setError(e.message));
  }, [open, character, personas]);

  if (!open || !character) return null;

  const greetings = card ? [card.first_mes, ...(card.alternate_greetings || [])] : [];
  const userName = personas.find((p) => p.id === Number(personaId))?.name || "You";

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const s = await api.createSession({
        character_id: character.id,
        persona_id: personaId === "" ? null : Number(personaId),
        greeting_index: greetingIndex,
        title: title.trim() || undefined,
      });
      onCreated?.(s.id);
      onClose();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={`New chat with ${character.name}`} width={580}>
      <Field label="Chat name">
        <Input value={title} onChange={(e) => setTitle(e.target.value)} />
      </Field>

      <Field label="Play as" hint="your persona in this scene">
        <div className="flex gap-2">
          <select
            value={personaId}
            onChange={(e) => setPersonaId(e.target.value)}
            className="flex-1 bg-ink-950 border border-ink-800 rounded-lg px-3 py-2 text-sm outline-none focus:border-accent/60"
          >
            <option value="">You (no persona)</option>
            {personas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <Button onClick={onManagePersonas}>Manage</Button>
        </div>
      </Field>

      <Field
        label="Opening message"
        hint={
          greetings.length > 1
            ? `${greetings.length} to choose from`
            : "from the character card"
        }
      >
        {!card ? (
          <p className="text-sm text-slate-600">Loading card…</p>
        ) : (
          <div className="space-y-2 max-h-56 overflow-y-auto scroll-thin">
            {greetings.map((g, i) => (
              <button
                key={i}
                onClick={() => setGreetingIndex(i)}
                className={`w-full text-left rounded-lg px-3 py-2.5 text-sm leading-relaxed border transition ${
                  greetingIndex === i
                    ? "border-accent/50 bg-accent/10"
                    : "border-ink-800 hover:border-ink-700"
                }`}
              >
                <div className="text-[11px] uppercase tracking-wider text-slate-600 mb-1">
                  {i === 0 ? "Default greeting" : `Alternate ${i}`}
                </div>
                <div className="text-slate-300 whitespace-pre-wrap">
                  {preview(g, card.name, userName) || (
                    <span className="text-slate-600">(empty)</span>
                  )}
                </div>
              </button>
            ))}
            {greetings.length === 0 && (
              <p className="text-sm text-slate-600">
                This card has no greeting. The chat will start empty.
              </p>
            )}
          </div>
        )}
      </Field>

      <div className="flex items-center gap-2 pt-1">
        <Button variant="primary" onClick={create} disabled={busy}>
          {busy ? "Starting…" : "Start chat"}
        </Button>
        <Button onClick={onClose}>Cancel</Button>
        {error && <span className="text-xs text-rose-400">{error}</span>}
      </div>
    </Modal>
  );
}
