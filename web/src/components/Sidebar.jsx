import { useRef } from "react";
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

  async function handleFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      if (file.name.toLowerCase().endsWith(".json")) {
        // A bundle (worlds + characters, app/routes/bundle.py) or a single
        // V1/V2 card exported as JSON.
        const data = JSON.parse(await file.text());
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
        className={`bg-ink-900 border-r border-ink-800 flex flex-col overflow-hidden
          fixed inset-y-0 left-0 z-40 w-72 transition-transform duration-200
          ${open ? "translate-x-0" : "-translate-x-full"}
          md:static md:translate-x-0 md:shrink-0 md:z-auto`}
      >
      <div className="p-4 border-b border-ink-800">
        <h1 className="font-semibold tracking-tight">Roleplay</h1>
        <button
          onClick={onManageModels}
          className="mt-1.5 flex items-center gap-1.5 text-xs hover:text-slate-300 transition w-full min-w-0"
          title="Manage models"
        >
          <span
            className={`w-1.5 h-1.5 rounded-full ${
              !health?.ok ? "bg-rose-400" : modelMissing ? "bg-amber-400" : "bg-emerald-400"
            }`}
          />
          <span className="text-slate-500 truncate min-w-0">
            {!health?.ok
              ? "backend offline"
              : modelMissing
              ? `${health.model} not installed`
              : health.model}
          </span>
        </button>
      </div>

      <div className="p-3 border-b border-ink-800 space-y-2">
        <button
          onClick={() => fileRef.current?.click()}
          className="w-full text-sm rounded-lg bg-accent/20 border border-accent/30 py-2 hover:bg-accent/30 transition"
        >
          Import character card
        </button>
        <button
          onClick={onNewCharacter}
          className="w-full text-xs rounded-lg border border-ink-700 py-1.5 hover:bg-ink-850 transition"
        >
          Write one from scratch
        </button>
        <div className="flex gap-2">
          <button
            onClick={onManagePersonas}
            className="flex-1 text-xs rounded-lg border border-ink-700 py-1.5 hover:bg-ink-850 transition"
          >
            Personas
          </button>
          <button
            onClick={onManageWorlds}
            className="flex-1 text-xs rounded-lg border border-ink-700 py-1.5 hover:bg-ink-850 transition"
          >
            Worlds
          </button>
          <button
            onClick={onManageModels}
            className="flex-1 text-xs rounded-lg border border-ink-700 py-1.5 hover:bg-ink-850 transition"
          >
            Models
          </button>
        </div>
        <input ref={fileRef} type="file" accept=".png,.json" hidden onChange={handleFile} />
      </div>

      <div className="flex-1 overflow-y-auto scroll-thin">
        <Section title={`Characters (${characters.length})`}>
          {characters.map((c) => (
            <div key={c.id} className="group flex items-center hover:bg-ink-850">
              <button
                onClick={() => { onNewChat(c); onClose?.(); }}
                className="flex items-center gap-2.5 px-3 py-2 text-left flex-1 min-w-0"
                title={c.description}
              >
                {/* Characters written by hand have no PNG behind them, and the
                    list already says so -- rendering the <img> anyway would fire
                    a guaranteed 404 per character on every load. */}
                {c.has_avatar ? (
                  <img
                    src={api.avatarUrl(c.id)}
                    alt=""
                    className="w-8 h-8 rounded-full object-cover bg-ink-700 shrink-0"
                    onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                  />
                ) : (
                  <div className="w-8 h-8 rounded-full bg-ink-700 shrink-0 grid place-items-center text-[11px] font-semibold text-slate-300">
                    {c.name.slice(0, 2).toUpperCase()}
                  </div>
                )}
                <span className="text-sm truncate">{c.name}</span>
              </button>
              <button
                onClick={() => { onEditCharacter(c.id); onClose?.(); }}
                className="px-2 text-xs text-slate-600 hover:text-slate-300 opacity-100 md:opacity-0 md:group-hover:opacity-100"
                title="Edit character"
              >
                edit
              </button>
            </div>
          ))}
          {characters.length === 0 && (
            <p className="px-3 py-2 text-xs text-slate-600">
              No characters yet — import a V2 card PNG above.
            </p>
          )}
        </Section>

        <Section title={`Chats (${sessions.length})`}>
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`group flex items-center ${
                s.id === activeSessionId ? "bg-ink-850" : ""
              }`}
            >
              <button
                onClick={() => { onOpenSession(s.id); onClose?.(); }}
                className="flex-1 px-3 py-2 text-left hover:bg-ink-850 min-w-0"
              >
                <div className="text-sm truncate">{s.title}</div>
                <div className="text-xs text-slate-600">{s.message_count} messages</div>
              </button>
              <button
                onClick={() => onDeleteSession(s.id)}
                className="px-2 text-slate-600 hover:text-rose-400 opacity-100 md:opacity-0 md:group-hover:opacity-100"
                title="Delete chat"
              >
                ×
              </button>
            </div>
          ))}
          {sessions.length === 0 && (
            <p className="px-3 py-2 text-xs text-slate-600">
              Click a character to start a chat.
            </p>
          )}
        </Section>
      </div>
      </aside>
    </>
  );
}

function Section({ title, children }) {
  return (
    <div className="py-2">
      <div className="px-3 py-1.5 text-[11px] uppercase tracking-wider text-slate-600">
        {title}
      </div>
      {children}
    </div>
  );
}
