import { useRef, useState } from "react";
import { api } from "../api";

export default function Sidebar({
  characters,
  sessions,
  activeSessionId,
  onOpenSession,
  onNewChat,
  onEditCharacter,
  onNewCharacter,
  onImported,
  onDeleteSession,
  onManagePersonas,
  onManageWorlds,
  onManageModels,
  health,
  open,
  onClose,
}) {
  const fileRef = useRef(null);
  const [chosenTab, setTab] = useState(null);
  // With no chats yet, start where something can be done. Worked out rather than set once:
  // the lists are empty for a moment on every load.
  const tab = chosenTab ?? (sessions.length === 0 ? "people" : "chats");

  async function handleFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      if (file.name.toLowerCase().endsWith(".json")) {
        // A bundle (worlds + characters, app/routes/bundle.py) or a single
        // V1/V2 card exported as JSON.
        const data = JSON.parse(await file.text());
        // A chat exported from any install (app/chat/transfer.py): always a new chat.
        if (data.format === "roleplay-chat") {
          const r = await api.importChat(data);
          const added = [
            r.characters_created.length && `added ${r.characters_created.join(", ")}`,
            r.persona_created && `added persona ${r.persona_created}`,
            r.worlds_created.length && `added world ${r.worlds_created.join(", ")}`,
          ].filter(Boolean);
          alert(`Imported "${r.title}": ${r.messages} messages${added.length ? `; ${added.join("; ")}` : ""}.`);
          await onImported?.();
          onOpenSession(r.id);
          onClose?.();
          return;
        }
        const bundle = data.format === "roleplay-bundle"
          ? data
          : { format: "roleplay-bundle", version: 1, characters: [{ card: data }] };
        const names = (bundle.characters || []).map((c) => c.card?.data?.name || c.card?.name).filter(Boolean);
        const replace = names.length > 0 && confirm(
          `Import ${names.length} character${names.length === 1 ? "" : "s"}` +
          `${bundle.worlds?.length ? ` and ${bundle.worlds.length} world${bundle.worlds.length === 1 ? "" : "s"}` : ""}.\n\n` +
          "OK: also update characters that already exist with the same name (their chats are kept).\n" +
          "Cancel: only add the new ones."
        );
        const r = await api.importBundle(bundle, replace);
        const parts = [
          r.characters_created.length && `added ${r.characters_created.join(", ")}`,
          r.characters_updated.length && `updated ${r.characters_updated.join(", ")}`,
          r.characters_skipped.length && `skipped ${r.characters_skipped.join(", ")} (already there)`,
          (r.worlds_created.length || r.worlds_updated.length) &&
            `world: ${[...r.worlds_created, ...r.worlds_updated].join(", ")}`,
        ].filter(Boolean);
        alert(`Imported -- ${parts.join("; ") || "nothing new"}.`);
      } else {
        await api.importCard(file);
      }
      onImported?.();
    } catch (err) {
      alert(`Import failed: ${err.message}`);
    } finally {
      e.target.value = "";
    }
  }

  // No answer yet isn't the same as a bad one: "offline" used to flash on every load.
  const checking = !health;
  const offline = health && !health.ok;
  const modelMissing = health?.ok && health?.model_installed === false;

  return (
    <>
      {/* Backdrop, mobile only. Tapping it closes the drawer the way a phone
          user expects; on desktop the sidebar is permanent and this never renders. */}
      {open && (
        <div
          className="fixed inset-0 bg-black/60 z-30 md:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      <aside
        // overflow-hidden: off-canvas on a phone, a long unbroken line (a full
        // Hugging Face model name) spilled out of the hidden drawer and across
        // the chat header. Seen live on an iPhone.
        className={`bg-ink-950 border-r border-ink-800 flex flex-col overflow-hidden
          fixed inset-y-0 left-0 z-40 w-72 transition-transform duration-200
          ${open ? "translate-x-0" : "-translate-x-full"}
          md:static md:w-64 md:translate-x-0 md:shrink-0 md:z-auto`}
      >
        <div className="px-5 pt-5 pb-3">
          <h1 className="font-serif text-[22px] font-semibold">Roleplay</h1>
          <button
            onClick={onManageModels}
            title={health?.model || "Manage models"}
            className="mt-1 flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-300 w-full min-w-0"
          >
            {(offline || modelMissing) && (
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${offline ? "bg-rose-400" : "bg-amber-400"}`} />
            )}
            <span className="truncate min-w-0">
              {checking
                ? "Connecting…"
                : offline
                ? "Backend offline"
                : modelMissing
                ? `${health.model} not installed`
                : "Model ready"}
            </span>
          </button>
        </div>

        <div className="px-5 flex gap-5 border-b border-ink-800 text-sm" role="tablist" aria-label="Sidebar">
          {[
            ["chats", `Chats (${sessions.length})`],
            ["people", `People (${characters.length})`],
          ].map(([id, label]) => (
            <button
              key={id}
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={`pb-2.5 -mb-px border-b-2 ${
                tab === id ? "border-accent text-accent" : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto scroll-thin py-2">
          {tab === "chats" &&
            sessions.map((s) => (
              <div key={s.id} className="group flex items-center">
                <button
                  onClick={() => { onOpenSession(s.id); onClose?.(); }}
                  aria-current={s.id === activeSessionId ? "true" : undefined}
                  className="flex-1 min-w-0 pl-5 pr-2 py-2.5 text-left"
                >
                  <div className={`text-sm truncate ${s.id === activeSessionId ? "text-accent" : "text-slate-200 group-hover:text-slate-100"}`}>
                    {s.title}
                  </div>
                  <div className="text-xs text-slate-500 truncate mt-0.5">
                    {[s.character_name, `${s.message_count} messages`].filter(Boolean).join(" · ")}
                  </div>
                </button>
                <button
                  onClick={() => onDeleteSession(s.id)}
                  aria-label={`Delete ${s.title}`}
                  title="Delete chat"
                  className="w-10 h-10 shrink-0 text-slate-500 hover:text-rose-400 opacity-100 md:opacity-0 md:group-hover:opacity-100 md:focus:opacity-100"
                >
                  ×
                </button>
              </div>
            ))}
          {tab === "chats" && sessions.length === 0 && (
            <p className="px-5 py-3 text-sm text-slate-500">Pick someone under People to start a chat.</p>
          )}

          {tab === "people" &&
            characters.map((c) => (
              <div key={c.id} className="group flex items-center">
                <button
                  onClick={() => { onNewChat(c); onClose?.(); }}
                  className="flex items-center gap-3 pl-5 pr-2 py-2 text-left flex-1 min-w-0"
                  title={c.description}
                >
                  {/* Characters written by hand have no PNG behind them, and the
                      list already says so -- rendering the <img> anyway would fire
                      a guaranteed 404 per character on every load. */}
                  {c.has_avatar ? (
                    <img
                      src={api.avatarUrl(c.id)}
                      alt=""
                      className="w-8 h-8 rounded-full object-cover bg-ink-800 shrink-0"
                      onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                    />
                  ) : (
                    <div className="w-8 h-8 rounded-full bg-ink-800 shrink-0 grid place-items-center font-serif text-xs text-slate-300">
                      {c.name.slice(0, 1).toUpperCase()}
                    </div>
                  )}
                  <span className="text-sm truncate text-slate-200 group-hover:text-slate-100">{c.name}</span>
                </button>
                <button
                  onClick={() => { onEditCharacter(c.id); onClose?.(); }}
                  aria-label={`Edit ${c.name}`}
                  title="Edit character"
                  className="px-3 py-2 text-xs text-slate-500 hover:text-slate-300 opacity-100 md:opacity-0 md:group-hover:opacity-100 md:focus:opacity-100"
                >
                  edit
                </button>
              </div>
            ))}
          {tab === "people" && characters.length === 0 && (
            <p className="px-5 py-3 text-sm text-slate-500">No characters yet. Import a card or write one below.</p>
          )}
        </div>

        <div className="border-t border-ink-800 p-3 space-y-1 text-sm">
          {tab === "people" ? (
            <button
              onClick={onNewCharacter}
              className="w-full min-h-[40px] rounded-lg border border-ink-700 text-slate-200 hover:border-accent/60"
            >
              Write a new character
            </button>
          ) : (
            <button
              onClick={() => setTab("people")}
              className="w-full min-h-[40px] rounded-lg border border-ink-700 text-slate-200 hover:border-accent/60"
            >
              New chat
            </button>
          )}
          <button
            onClick={() => fileRef.current?.click()}
            className="w-full min-h-[36px] text-slate-400 hover:text-slate-200"
          >
            Import a card or chat
          </button>
          <div className="flex justify-between px-2 pt-1 text-xs text-slate-500">
            <button onClick={onManagePersonas} className="py-1.5 hover:text-slate-300">Personas</button>
            <button onClick={onManageWorlds} className="py-1.5 hover:text-slate-300">Worlds</button>
            <button onClick={onManageModels} className="py-1.5 hover:text-slate-300">Models</button>
          </div>
          <input ref={fileRef} type="file" accept=".png,.json" hidden onChange={handleFile} />
        </div>
      </aside>
    </>
  );
}
