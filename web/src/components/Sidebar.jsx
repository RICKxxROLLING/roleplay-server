import { useRef } from "react";
import { api } from "../api";

export default function Sidebar({
  characters,
  sessions,
  activeSessionId,
  onOpenSession,
  onNewChat,
  onEditCharacter,
  onImported,
  onDeleteSession,
  onManagePersonas,
  onManageModels,
  health,
}) {
  const fileRef = useRef(null);

  async function handleFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      await api.importCard(file);
      onImported?.();
    } catch (err) {
      alert(`Import failed: ${err.message}`);
    } finally {
      e.target.value = "";
    }
  }

  const modelMissing = health?.ok && health?.model_installed === false;

  return (
    <aside className="w-72 shrink-0 bg-ink-900 border-r border-ink-800 flex flex-col">
      <div className="p-4 border-b border-ink-800">
        <h1 className="font-semibold tracking-tight">Roleplay</h1>
        <button
          onClick={onManageModels}
          className="mt-1.5 flex items-center gap-1.5 text-xs hover:text-slate-300 transition"
          title="Manage models"
        >
          <span
            className={`w-1.5 h-1.5 rounded-full ${
              !health?.ok ? "bg-rose-400" : modelMissing ? "bg-amber-400" : "bg-emerald-400"
            }`}
          />
          <span className="text-slate-500 truncate">
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
        <div className="flex gap-2">
          <button
            onClick={onManagePersonas}
            className="flex-1 text-xs rounded-lg border border-ink-700 py-1.5 hover:bg-ink-850 transition"
          >
            Personas
          </button>
          <button
            onClick={onManageModels}
            className="flex-1 text-xs rounded-lg border border-ink-700 py-1.5 hover:bg-ink-850 transition"
          >
            Models
          </button>
        </div>
        <input ref={fileRef} type="file" accept=".png" hidden onChange={handleFile} />
      </div>

      <div className="flex-1 overflow-y-auto scroll-thin">
        <Section title={`Characters (${characters.length})`}>
          {characters.map((c) => (
            <div key={c.id} className="group flex items-center hover:bg-ink-850">
              <button
                onClick={() => onNewChat(c)}
                className="flex items-center gap-2.5 px-3 py-2 text-left flex-1 min-w-0"
                title={c.description}
              >
                <img
                  src={api.avatarUrl(c.id)}
                  alt=""
                  className="w-8 h-8 rounded-full object-cover bg-ink-700 shrink-0"
                  onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                />
                <span className="text-sm truncate">{c.name}</span>
              </button>
              <button
                onClick={() => onEditCharacter(c.id)}
                className="px-2 text-xs text-slate-600 hover:text-slate-300 opacity-0 group-hover:opacity-100"
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
                onClick={() => onOpenSession(s.id)}
                className="flex-1 px-3 py-2 text-left hover:bg-ink-850 min-w-0"
              >
                <div className="text-sm truncate">{s.title}</div>
                <div className="text-xs text-slate-600">{s.message_count} messages</div>
              </button>
              <button
                onClick={() => onDeleteSession(s.id)}
                className="px-2 text-slate-600 hover:text-rose-400 opacity-0 group-hover:opacity-100"
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
