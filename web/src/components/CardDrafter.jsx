import { useEffect, useRef, useState } from "react";
import { streamTurn } from "../api";
import { Button, Textarea } from "./ui";

const LABELS = {
  name: "Name",
  description: "Description",
  personality: "Personality",
  scenario: "Scenario",
  first_mes: "Opening message",
  mes_example: "Example dialogue",
};

/**
 * Draft a card with the model (app/cards/creator.py), then pick what to keep.
 *
 * Nothing is saved from here: `onApply` hands the chosen fields to the form
 * around it, and the usual Create / Save is what writes them. Expanding a card
 * (`characterId` set) pre-ticks only fields that are empty or that the draft
 * makes longer, so a hand-written greeting isn't swapped out unnoticed.
 */
export default function CardDrafter({ characterId, current = {}, worlds = [], worldId, onApply }) {
  const [brief, setBrief] = useState("");
  const [world, setWorld] = useState(worldId ?? "");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState("");
  const [draft, setDraft] = useState(null);
  const [picked, setPicked] = useState({});
  const [error, setError] = useState(null);
  const abortRef = useRef(null);
  const expanding = !!characterId;

  useEffect(() => () => abortRef.current?.(), []);
  useEffect(() => setWorld(worldId ?? ""), [worldId]);

  function run() {
    setBusy(true);
    setError(null);
    setDraft(null);
    setPreview("");
    let acc = "";
    abortRef.current = streamTurn(
      "/characters/draft",
      {
        brief,
        world_id: world === "" ? null : Number(world),
        character_id: characterId ?? null,
      },
      {
        onToken: (t) => {
          acc += t;
          setPreview(acc);
        },
        onEvent: (evt) => {
          if (evt.type !== "draft") return;
          setDraft(evt);
          const pick = {};
          for (const [k, v] of Object.entries(evt.fields)) {
            const now = (current[k] || "").trim();
            pick[k] = !expanding || !now || v.length > now.length;
          }
          setPicked(pick);
        },
        onDone: () => setBusy(false),
        onError: (e) => {
          setBusy(false);
          setError(e.message);
        },
      }
    );
  }

  function apply() {
    const chosen = Object.fromEntries(
      Object.entries(draft.fields).filter(([k]) => picked[k])
    );
    onApply?.(chosen);
    setDraft(null);
    setPreview("");
  }

  return (
    <div className="border border-ink-800 rounded-lg p-3 mb-4 bg-ink-900/40">
      {!expanding && (
        <Textarea
          rows={2}
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
          placeholder="Describe an idea: a grumpy lighthouse keeper who secretly writes poetry…"
        />
      )}
      {expanding && (
        <Textarea
          rows={1}
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
          placeholder="Optional: what to add, e.g. a backstory with her old theatre troupe"
        />
      )}
      <div className="flex flex-wrap items-center gap-2 mt-2">
        {!expanding && worlds.length > 0 && (
          <select
            value={world}
            onChange={(e) => setWorld(e.target.value)}
            className="bg-ink-950 border border-ink-800 rounded-lg px-2 py-1.5 text-xs outline-none focus:border-accent/60"
          >
            <option value="">No world</option>
            {worlds.map((w) => (
              <option key={w.id} value={w.id}>
                Set in {w.name}
              </option>
            ))}
          </select>
        )}
        {busy ? (
          <Button onClick={() => { abortRef.current?.(); setBusy(false); }}>Stop</Button>
        ) : (
          <Button onClick={run} disabled={!expanding && !brief.trim()}>
            {expanding ? "Flesh out with the model" : "Draft with the model"}
          </Button>
        )}
        <span className="text-[11px] text-slate-600">
          about 10 s on the GPU · nothing is saved until you {expanding ? "save" : "create"}
        </span>
      </div>

      {busy && preview && (
        <pre className="mt-3 max-h-48 overflow-y-auto scroll-thin whitespace-pre-wrap text-xs text-slate-400">
          {preview}
        </pre>
      )}
      {error && <p className="text-xs text-rose-400 mt-2">{error}</p>}

      {draft && (
        <div className="mt-3 space-y-2">
          {draft.name_taken && (
            <p className="text-xs text-amber-400">
              A character named {draft.fields.name} already exists. Two with one name make group chats
              and lore ambiguous; consider renaming.
            </p>
          )}
          {draft.missing.length > 0 && (
            <p className="text-xs text-slate-500">
              Not drafted: {draft.missing.join(", ")}. Draft again, or write them below.
            </p>
          )}
          {Object.entries(draft.fields).map(([k, v]) => (
            <label key={k} className="flex gap-2 items-start text-xs cursor-pointer">
              <input
                type="checkbox"
                checked={!!picked[k]}
                onChange={(e) => setPicked({ ...picked, [k]: e.target.checked })}
                className="mt-0.5 accent-violet-400"
              />
              <span className="min-w-0">
                <span className="text-slate-300">{LABELS[k] || k}</span>
                {expanding && (current[k] || "").trim() && (
                  <span className="text-slate-600"> · replaces what's there</span>
                )}
                <span className="block text-slate-500 whitespace-pre-wrap line-clamp-3">{v}</span>
              </span>
            </label>
          ))}
          <div className="flex gap-2 pt-1">
            <Button variant="primary" onClick={apply} disabled={!Object.values(picked).some(Boolean)}>
              Use ticked fields
            </Button>
            <Button onClick={run}>Draft again</Button>
          </div>
        </div>
      )}
    </div>
  );
}
