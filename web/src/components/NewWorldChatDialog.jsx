import { useEffect, useState } from "react";
import { api } from "../api";
import { Modal, Field, Input, Button } from "./ui";

/** A chat based in a world: people come and go, and nobody here is the main character. */
export default function NewWorldChatDialog({ world, personas, onClose, onCreated, onManagePersonas }) {
  const [title, setTitle] = useState("");
  const [personaId, setPersonaId] = useState("");
  const [place, setPlace] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!world) return;
    setTitle(world.name);
    setPersonaId(personas[0]?.id ?? "");
    setPlace("");
    setError(null);
  }, [world, personas]);

  if (!world) return null;

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const s = await api.createWorldChat({
        world_id: world.id,
        persona_id: personaId === "" ? null : Number(personaId),
        title: title.trim() || undefined,
        place: place.trim(),
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
    <Modal open onClose={onClose} title={`World chat in ${world.name}`} width={520}>
      <p className="text-xs text-slate-500 mb-4">
        You meet people one at a time. They speak first, and nobody leaves unless you do. Walk on when
        you&apos;re done, and what you said stays with the person you said it to.
      </p>
      <Field label="Chat name">
        <Input value={title} onChange={(e) => setTitle(e.target.value)} />
      </Field>
      <Field label="Play as" hint="everyone you meet reads this, so keep it about you">
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
      <Field label="Where to begin" hint="optional: leave it to the world">
        <Input value={place} onChange={(e) => setPlace(e.target.value)} placeholder="the general store" maxLength={60} />
      </Field>
      <div className="flex items-center gap-2 pt-1">
        <Button variant="primary" onClick={start} disabled={busy}>
          {busy ? "Arriving…" : "Start"}
        </Button>
        <Button onClick={onClose}>Cancel</Button>
        {error && <span className="text-xs text-rose-400">{error}</span>}
      </div>
    </Modal>
  );
}
