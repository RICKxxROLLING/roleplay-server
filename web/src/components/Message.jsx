import { useEffect, useState } from "react";
import { api } from "../api";
import { isPlaying, onPlayerChange, play, speechPath, stopPlaying } from "../player";

/** Read this message aloud, or stop it. The first use of a voice takes a moment to download. */
function ListenButton({ sessionId, messageId, onError }) {
  const path = speechPath(sessionId, messageId);
  const [on, setOn] = useState(isPlaying(path));
  useEffect(() => onPlayerChange(() => setOn(isPlaying(path))), [path]);
  return (
    <button
      onClick={() => (on ? stopPlaying() : play(path, onError))}
      className="hover:text-slate-300"
      title="Read this aloud"
    >
      {on ? "stop" : "listen"}
    </button>
  );
}

/** Render *action text* as italic narration, the roleplay convention. */
function RichText({ text, tone = "text-slate-400" }) {
  const parts = text.split(/(\*[^*]+\*)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("*") && p.endsWith("*") && p.length > 2 ? (
          <em key={i} className={`${tone} italic`}>
            {p.slice(1, -1)}
          </em>
        ) : (
          <span key={i}>{p}</span>
        )
      )}
    </>
  );
}

/** The narrator (app/chat/narrator.py): part of the scene, nobody's turn. */
function NarratorLine({ msg, sessionId, onChanged }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(msg.content);
  const [note, setNote] = useState(null);

  async function save() {
    if (!draft.trim()) return;
    await api.editMessage(sessionId, msg.id, draft);
    setEditing(false);
    onChanged?.();
  }

  async function remove() {
    await api.deleteMessage(sessionId, msg.id);
    onChanged?.();
  }

  return (
    <div className="group px-2 sm:px-8 text-center">
      {editing ? (
        <div className="text-left">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={2}
            autoFocus
            className="w-full bg-ink-950 border border-ink-700 rounded-xl px-3 py-2 text-sm outline-none focus:border-accent/60 resize-y"
          />
          <div className="flex gap-2 mt-1.5">
            <button onClick={save} className="text-xs px-2.5 py-1 rounded-lg bg-accent/25 border border-accent/40 hover:bg-accent/35">
              Save
            </button>
            <button onClick={() => setEditing(false)} className="text-xs px-2.5 py-1 rounded-lg border border-ink-700 hover:bg-ink-850">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <p className="font-serif text-[15px] sm:text-base italic text-slate-500 leading-relaxed break-words">{msg.content}</p>
          {typeof msg.id === "number" && (
            <span className="text-xs text-slate-500 inline-flex gap-3 mt-1 opacity-100 md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100 transition">
              <ListenButton sessionId={sessionId} messageId={msg.id} onError={setNote} />
              <button
                onClick={() => {
                  setDraft(msg.content);
                  setEditing(true);
                }}
                className="hover:text-slate-300"
              >
                edit
              </button>
              <button onClick={remove} className="hover:text-rose-400">
                delete
              </button>
            </span>
          )}
          {note && <p className="text-xs text-rose-400 mt-1">{note}</p>}
        </>
      )}
    </div>
  );
}

function Turn({
  msg,
  charName,
  showName,
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
  const [showActions, setShowActions] = useState(false);

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

  // Touch has no hover: a tap on the message brings its actions up.
  const actions = editable && !editing && (
    <div
      className={`${showActions ? "flex" : "hidden"} md:flex md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100 transition gap-3 mt-1.5 font-sans text-xs text-slate-500 ${
        isUser ? "pl-4" : ""
      }`}
    >
      {!isUser && <ListenButton sessionId={sessionId} messageId={msg.id} onError={setNote} />}
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
    </div>
  );

  return (
    <div className="group min-w-0" onClick={(e) => e.target.tagName !== "BUTTON" && setShowActions((v) => !v)}>
      {(showName || msg.initiated || msg.pinned) && !isUser && (
        <div className="mb-1 flex gap-2 items-baseline text-xs text-slate-500">
          {showName && <span className="text-slate-300 font-medium">{name}</span>}
          {msg.initiated && <span title="Sent on their own, without a message from you">wrote first</span>}
          {msg.pinned && (
            <span className="text-accent" title="Kept word for word in the prompt, even after the summary condenses it">
              pinned
            </span>
          )}
        </div>
      )}

      {editing ? (
        <div onClick={(e) => e.stopPropagation()}>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={Math.min(12, Math.max(3, draft.split("\n").length + 1))}
            autoFocus
            className="w-full bg-ink-850 border border-ink-700 rounded-lg px-3 py-2.5 text-[15px] leading-relaxed outline-none focus:border-accent/60 resize-y"
          />
          <div className="flex gap-2 mt-1.5">
            <button onClick={save} className="text-xs px-3 py-1.5 rounded-lg bg-accent text-ink-950 font-medium">
              Save
            </button>
            <button onClick={() => setEditing(false)} className="text-xs px-3 py-1.5 rounded-lg border border-ink-700 hover:bg-ink-850">
              Cancel
            </button>
          </div>
        </div>
      ) : msg.texted ? (
        // Texting mode: one phone bubble per line, the way a thread looks.
        <div className={`flex flex-col gap-1 ${isUser ? "items-end" : "items-start"}`}>
          {msg.content.split("\n").filter((l) => l.trim()).map((line, i, all) => (
            <div
              key={i}
              className={`max-w-[85%] text-left px-3.5 py-2 text-[15px] leading-snug break-words ${
                isUser
                  ? "bg-accent text-ink-950 rounded-2xl rounded-br-md"
                  : "bg-ink-800 text-slate-100 rounded-2xl rounded-bl-md"
              } ${streaming && i === all.length - 1 ? "caret" : ""}`}
            >
              {line}
            </div>
          ))}
        </div>
      ) : isUser ? (
        // Yours is set apart by its type and colour, as a note in the margin of the story.
        <div className="pl-4 text-[15px] leading-relaxed text-accent whitespace-pre-wrap break-words">
          <RichText text={msg.content} tone="text-accent/80" />
        </div>
      ) : (
        <div
          className={`font-serif text-[18px] sm:text-[19px] leading-[1.62] text-slate-200 whitespace-pre-wrap break-words ${
            streaming ? "caret" : ""
          }`}
        >
          <RichText text={msg.content} />
        </div>
      )}

      {swipeable && !isUser && !editing && (
        <div className="flex items-center gap-1 mt-1 text-xs text-slate-500 font-sans">
          <button
            onClick={() => swipeTo(msg.swipe_index - 1)}
            disabled={!msg.swipes || msg.swipe_index === 0}
            className="w-8 h-8 hover:text-slate-300 disabled:opacity-30"
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
            className="w-8 h-8 hover:text-slate-300"
            aria-label="Next version"
            title={msg.swipe_index < (msg.swipes ?? 1) - 1 ? "Next version" : "Write another version"}
          >
            ›
          </button>
        </div>
      )}

      {actions}

      {note && <div className="text-xs text-amber-300 mt-1">{note}</div>}
    </div>
  );
}

export default function Message(props) {
  // Two components: a narrator line has none of a turn's avatar, swipes or pins.
  return props.msg.role === "narrator" ? (
    <NarratorLine msg={props.msg} sessionId={props.sessionId} onChanged={props.onChanged} />
  ) : (
    <Turn {...props} />
  );
}
