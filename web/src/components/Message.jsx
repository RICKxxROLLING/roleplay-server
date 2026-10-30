import { useState } from "react";
import { api } from "../api";

/** Render *action text* as italic narration, the roleplay convention. */
function RichText({ text }) {
  const parts = text.split(/(\*[^*]+\*)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("*") && p.endsWith("*") && p.length > 2 ? (
          <em key={i} className="text-slate-400 italic">
            {p.slice(1, -1)}
          </em>
        ) : (
          <span key={i}>{p}</span>
        )
      )}
    </>
  );
}

export default function Message({
  msg,
  charName,
  charId,
  userName,
  streaming,
  sessionId,
  onChanged,
  // Only the latest reply gets swipe controls; the server enforces the same.
  swipeable,
  onRegenerate,
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(msg.content);
  const [note, setNote] = useState(null);
  const [avatarFailed, setAvatarFailed] = useState(false);

  const isUser = msg.role === "user";
  const name = isUser ? userName || "You" : charName || "Character";
  // Optimistic messages have string ids and aren't persisted yet.
  const editable = !streaming && sessionId && typeof msg.id === "number";

  async function save() {
    if (!draft.trim()) return;
    try {
      const r = await api.editMessage(sessionId, msg.id, draft);
      setEditing(false);
      setNote(r.note || null);
      onChanged?.();
    } catch (e) {
      setNote(e.message);
    }
  }

  async function swipeTo(index) {
    try {
      await api.swipe(sessionId, msg.id, index);
      onChanged?.();
    } catch (e) {
      setNote(e.message);
    }
  }

  async function togglePin() {
    try {
      await api.setPinned(sessionId, msg.id, !msg.pinned);
      onChanged?.();
    } catch (e) {
      setNote(e.message);
    }
  }

  async function remove() {
    if (!confirm("Delete this message?")) return;
    try {
      await api.deleteMessage(sessionId, msg.id);
      onChanged?.();
    } catch (e) {
      setNote(e.message);
    }
  }

  return (
    <div className={`group flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      <div className="shrink-0">
        {/* Falls back to initials rather than hiding, since a character written
            by hand has no avatar and would otherwise leave a blank gap on every
            one of its messages. */}
        {!isUser && charId && !avatarFailed ? (
          <img
            src={api.avatarUrl(charId)}
            alt={name}
            className="w-9 h-9 rounded-full object-cover bg-ink-700"
            onError={() => setAvatarFailed(true)}
          />
        ) : (
          <div className="w-9 h-9 rounded-full bg-ink-700 grid place-items-center text-xs font-semibold text-slate-300">
            {name.slice(0, 2).toUpperCase()}
          </div>
        )}
      </div>

      <div className={`max-w-[85%] sm:max-w-[72%] min-w-0 ${isUser ? "text-right" : ""}`}>
        <div className="text-xs text-slate-500 mb-1 flex items-center gap-2">
          <span className={isUser ? "ml-auto" : ""}>{name}</span>
          {msg.initiated && (
            <span
              className="text-[10px] px-1.5 rounded-full bg-ink-800 border border-ink-700 text-slate-400"
              title="Sent on their own, without a message from you"
            >
              wrote first
            </span>
          )}
          {msg.pinned && (
            <span
              className="text-[10px] px-1.5 rounded-full bg-accent/15 border border-accent/30 text-accent/90"
              title="Kept word for word in the prompt, even after the summary condenses it"
            >
              pinned
            </span>
          )}
          {/* Always visible on touch: there is no hover, so these were unreachable. */}
          {editable && !editing && (
            <span className="opacity-100 md:opacity-0 md:group-hover:opacity-100 flex gap-1.5 transition">
              <button
                onClick={() => {
                  setDraft(msg.content);
                  setEditing(true);
                }}
                className="hover:text-slate-300"
              >
                edit
              </button>
              <button
                onClick={togglePin}
                className="hover:text-slate-300"
                title="Keep this message word for word in the character's memory"
              >
                {msg.pinned ? "unpin" : "pin"}
              </button>
              <button onClick={remove} className="hover:text-rose-400">
                delete
              </button>
            </span>
          )}
        </div>

        {editing ? (
          <div className="text-left">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={Math.min(12, Math.max(3, draft.split("\n").length + 1))}
              autoFocus
              className="w-full bg-ink-950 border border-ink-700 rounded-xl px-3 py-2.5 text-sm leading-relaxed outline-none focus:border-accent/60 resize-y"
            />
            <div className="flex gap-2 mt-1.5">
              <button
                onClick={save}
                className="text-xs px-2.5 py-1 rounded-lg bg-accent/25 border border-accent/40 hover:bg-accent/35"
              >
                Save
              </button>
              <button
                onClick={() => setEditing(false)}
                className="text-xs px-2.5 py-1 rounded-lg border border-ink-700 hover:bg-ink-850"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div
            className={`inline-block text-left rounded-2xl px-4 py-2.5 whitespace-pre-wrap leading-relaxed ${
              isUser
                ? "bg-accent/20 border border-accent/30"
                : "bg-ink-850 border border-ink-700"
            } ${streaming ? "caret" : ""}`}
          >
            <RichText text={msg.content} />
          </div>
        )}

        {swipeable && !isUser && !editing && (
          <div className="flex items-center gap-1 mt-1 text-xs text-slate-500">
            <button
              onClick={() => swipeTo(msg.swipe_index - 1)}
              disabled={!msg.swipes || msg.swipe_index === 0}
              className="px-1.5 hover:text-slate-300 disabled:opacity-30"
              aria-label="Previous version"
            >
              ‹
            </button>
            <span className="tabular-nums">
              {(msg.swipe_index ?? 0) + 1}/{msg.swipes ?? 1}
            </span>
            {/* Past the newest version, the arrow writes another one. */}
            <button
              onClick={() =>
                msg.swipe_index < (msg.swipes ?? 1) - 1
                  ? swipeTo(msg.swipe_index + 1)
                  : onRegenerate?.()
              }
              className="px-1.5 hover:text-slate-300"
              aria-label="Next version"
              title={msg.swipe_index < (msg.swipes ?? 1) - 1 ? "Next version" : "Write another version"}
            >
              ›
            </button>
          </div>
        )}

        {note && (
          <div className="text-[11px] text-amber-400/80 mt-1 text-left">{note}</div>
        )}
      </div>
    </div>
  );
}
