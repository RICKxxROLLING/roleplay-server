import { useState } from "react";
import { Field, Input, Textarea, Button, Empty } from "./ui";

const BLANK = {
  keys: [],
  content: "",
  enabled: true,
  insertion_order: 100,
  case_sensitive: false,
  priority: 10,
  selective: false,
  secondary_keys: [],
  constant: false,
  position: "after_char",
  name: "",
  comment: "",
};

const keysToText = (a) => (a || []).join(", ");
const textToKeys = (s) =>
  s
    .split(",")
    .map((k) => k.trim())
    .filter(Boolean);

/** Editor for a character's keyword-triggered world info. */
export default function LorebookEditor({ form, set }) {
  const [openIdx, setOpenIdx] = useState(null);
  const entries = form.character_book || [];

  const update = (i, patch) =>
    set(
      "character_book",
      entries.map((e, x) => (x === i ? { ...e, ...patch } : e))
    );

  const remove = (i) =>
    set("character_book", entries.filter((_, x) => x !== i));

  const add = () => {
    set("character_book", [...entries, { ...BLANK }]);
    setOpenIdx(entries.length);
  };

  return (
    <div className="border-t border-ink-800 pt-4 mt-4">
      <div className="flex items-baseline justify-between mb-2">
        <h3 className="text-sm">Lorebook</h3>
        <span className="text-xs text-slate-600">
          {entries.length} {entries.length === 1 ? "entry" : "entries"}
        </span>
      </div>
      <p className="text-xs text-slate-500 mb-3">
        World facts injected only when their keywords appear in recent messages.
        Costs no context until triggered.
      </p>

      <div className="grid grid-cols-3 gap-2 mb-4">
        <Field label="Scan depth" hint="">
          <Input
            type="number"
            min={1}
            max={50}
            value={form.lorebook_scan_depth ?? 4}
            onChange={(e) =>
              set("lorebook_scan_depth", Math.max(1, Number(e.target.value)))
            }
          />
        </Field>
        <Field label="Token budget" hint="">
          <Input
            type="number"
            min={0}
            max={4000}
            step={50}
            value={form.lorebook_token_budget ?? 400}
            onChange={(e) =>
              set("lorebook_token_budget", Math.max(0, Number(e.target.value)))
            }
          />
        </Field>
        <div className="mb-4">
          <label className="text-sm block mb-1.5">Recursive</label>
          <label className="flex items-center gap-2 text-xs text-slate-500 h-[38px]">
            <input
              type="checkbox"
              checked={!!form.lorebook_recursive}
              onChange={(e) => set("lorebook_recursive", e.target.checked)}
              className="accent-accent w-4 h-4"
            />
            entries trigger entries
          </label>
        </div>
      </div>

      <div className="space-y-2">
        {entries.map((e, i) => {
          const open = openIdx === i;
          return (
            <div
              key={i}
              className={`border rounded-lg ${
                e.enabled === false ? "border-ink-800 opacity-50" : "border-ink-800"
              }`}
            >
              <div className="flex items-center gap-2 px-3 py-2">
                <input
                  type="checkbox"
                  checked={e.enabled !== false}
                  onChange={(ev) => update(i, { enabled: ev.target.checked })}
                  title="Enabled"
                  className="accent-accent w-3.5 h-3.5 shrink-0"
                />
                <button
                  onClick={() => setOpenIdx(open ? null : i)}
                  className="flex-1 text-left min-w-0"
                >
                  <div className="text-sm truncate">
                    {e.name || keysToText(e.keys) || (
                      <span className="text-slate-600">
                        {e.constant ? "(always on)" : "(no keys — never fires)"}
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-slate-600 truncate">
                    {e.content || "empty"}
                  </div>
                </button>
                {e.constant && (
                  <span className="text-[10px] text-accent/80 shrink-0">always</span>
                )}
                <Button variant="danger" onClick={() => remove(i)}>
                  ×
                </Button>
              </div>

              {open && (
                <div className="px-3 pb-3 pt-1 border-t border-ink-800">
                  <Field label="Name" hint="label only — never sent to the model">
                    <Input
                      value={e.name || ""}
                      onChange={(ev) => update(i, { name: ev.target.value })}
                      placeholder="The Archive fire"
                    />
                  </Field>

                  <Field label="Keywords" hint="comma separated, whole-word match">
                    <Input
                      value={keysToText(e.keys)}
                      onChange={(ev) => update(i, { keys: textToKeys(ev.target.value) })}
                      placeholder="archive, library"
                    />
                  </Field>

                  <Field label="Content" hint="injected verbatim when triggered">
                    <Textarea
                      rows={3}
                      value={e.content || ""}
                      onChange={(ev) => update(i, { content: ev.target.value })}
                      placeholder="The Archive burned in 1721. Nobody knows who set the fire."
                    />
                  </Field>

                  <div className="flex flex-wrap gap-4 mb-3">
                    <label className="flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={!!e.constant}
                        onChange={(ev) => update(i, { constant: ev.target.checked })}
                        className="accent-accent w-3.5 h-3.5"
                      />
                      Always inject
                    </label>
                    <label className="flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={!!e.case_sensitive}
                        onChange={(ev) =>
                          update(i, { case_sensitive: ev.target.checked })
                        }
                        className="accent-accent w-3.5 h-3.5"
                      />
                      Case sensitive
                    </label>
                    <label className="flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={!!e.selective}
                        onChange={(ev) => update(i, { selective: ev.target.checked })}
                        className="accent-accent w-3.5 h-3.5"
                      />
                      Require a second keyword
                    </label>
                  </div>

                  {e.selective && (
                    <Field
                      label="Secondary keywords"
                      hint="one of these must ALSO appear"
                    >
                      <Input
                        value={keysToText(e.secondary_keys)}
                        onChange={(ev) =>
                          update(i, { secondary_keys: textToKeys(ev.target.value) })
                        }
                        placeholder="fire, burned"
                      />
                    </Field>
                  )}

                  <div className="grid grid-cols-3 gap-2">
                    <Field label="Order" hint="">
                      <Input
                        type="number"
                        value={e.insertion_order ?? 100}
                        onChange={(ev) =>
                          update(i, { insertion_order: Number(ev.target.value) })
                        }
                      />
                    </Field>
                    <Field label="Priority" hint="">
                      <Input
                        type="number"
                        value={e.priority ?? 10}
                        onChange={(ev) => update(i, { priority: Number(ev.target.value) })}
                      />
                    </Field>
                    <Field label="Position" hint="">
                      <select
                        value={e.position || "after_char"}
                        onChange={(ev) => update(i, { position: ev.target.value })}
                        className="w-full bg-ink-950 border border-ink-800 rounded-lg px-3 py-2 text-sm outline-none focus:border-accent/60"
                      >
                        <option value="before_char">Before character</option>
                        <option value="after_char">After character</option>
                      </select>
                    </Field>
                  </div>
                  <p className="text-[11px] text-slate-600 -mt-2">
                    Lower order goes first. When the budget is tight, the lowest
                    priority is dropped first.
                  </p>
                </div>
              )}
            </div>
          );
        })}

        {entries.length === 0 && <Empty>No lore entries.</Empty>}
      </div>

      <Button className="mt-3" onClick={add}>
        Add entry
      </Button>
    </div>
  );
}
