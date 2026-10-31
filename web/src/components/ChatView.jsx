import { useEffect, useRef, useState } from "react";
import { api, streamTurn, stripSpeakerPrefix } from "../api";
import Message from "./Message";

export default function ChatView({
  sessionId,
  personas,
  characters = [],
  onOpenSettings,
  onOpenMemory,
  onOpenJournal,
  onDirty,
  onOpenNav,
}) {
  const [session, setSession] = useState(null);
  const [draft, setDraft] = useState("");
  const [streamText, setStreamText] = useState("");
  const [busy, setBusy] = useState(false);
  const [memoryState, setMemoryState] = useState(null);
  const [guardRemoved, setGuardRemoved] = useState(0);
  const [error, setError] = useState(null);
  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  // Group chats: who is writing the reply being streamed.
  const [streamSpeaker, setStreamSpeaker] = useState(null);
  const [castOpen, setCastOpen] = useState(false);
  const abortRef = useRef(null);
  const bottomRef = useRef(null);

  const reload = () =>
    api.session(sessionId).then(setSession).catch((e) => setError(e.message));

  async function saveTitle() {
    setRenaming(false);
    const title = titleDraft.trim();
    if (!title || title === session?.title) return;
    try {
      await api.updateSession(sessionId, { title });
      await reload();
      onDirty?.();
    } catch (e) {
      setError(e.message);
    }
  }

  async function setMode(mode) {
    try {
      await api.updateSession(sessionId, { mode });
      await reload();
    } catch (e) {
      setError(e.message);
    }
  }

  async function setAutonomous(on) {
    try {
      await api.updateSession(sessionId, { autonomous: on });
      await reload();
    } catch (e) {
      setError(e.message);
    }
  }

  // "Message me now": one message, skipping the timing rules and the
  // would-they pass. Streams nothing -- the reply arrives on reload.
  const [nudging, setNudging] = useState(false);
  async function messageMeNow() {
    setNudging(true);
    setError(null);
    try {
      const r = await api.writeFirst(sessionId, true);
      if (!r.sent) setError(`Nothing was sent: ${r.why}`);
      await reload();
      onDirty?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setNudging(false);
    }
  }

  async function swapPersona(value) {
    try {
      await api.updateSession(
        sessionId,
        value === "" ? { clear_persona: true } : { persona_id: Number(value) }
      );
      await reload();
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    setStreamText("");
    setMemoryState(null);
    setError(null);
    if (sessionId) api.session(sessionId).then(setSession).catch((e) => setError(e.message));
    else setSession(null);
  }, [sessionId]);

  // Follow new text only while the reader is at the bottom, and jump rather
  // than animate: a smooth scroll restarted on every token stutters, and
  // pulling someone down while they reread an earlier message is worse.
  const scrollRef = useRef(null);
  const stickRef = useRef(true);
  function onScroll() {
    const el = scrollRef.current;
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  }
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [session?.messages?.length, streamText]);
  // A newly opened chat starts at its latest message.
  useEffect(() => {
    stickRef.current = true;
  }, [sessionId]);

  const [menuOpen, setMenuOpen] = useState(false);

  const charName = session?.character_name;
  const cast = session?.cast || [];
  const isGroup = cast.length > 1;
  const writerName = streamSpeaker?.name || charName;
  const writerId = streamSpeaker?.id || session?.character_id;

  async function setMembers(ids) {
    try {
      await api.setMembers(sessionId, ids);
      await reload();
      onDirty?.();
    } catch (e) {
      setError(e.message);
    }
  }
  const userName = personas.find((p) => p.id === session?.persona_id)?.name || "You";

  function runStream(path, body) {
    setBusy(true);
    setError(null);
    setStreamText("");
    setGuardRemoved(0);
    setStreamSpeaker(null);
    let acc = "";
    let speaker = charName;

    abortRef.current = streamTurn(path, body, {
      onEvent: (evt) => {
        if (evt.type === "guard") setGuardRemoved(evt.removed);
        if (evt.type === "speaker") {
          speaker = evt.name;
          setStreamSpeaker({ id: evt.id, name: evt.name });
        }
        if (evt.type === "scene") setSession((s) => (s ? { ...s, scene: evt.scene } : s));
        // The server is regenerating; what streamed so far is discarded.
        if (evt.type === "redo") {
          acc = "";
          setStreamText("");
        }
      },
      onToken: (t) => {
        acc += t;
        setStreamText(stripSpeakerPrefix(acc, speaker));
      },
      onMemory: (evt) => {
        if (evt.status === "compressing") setMemoryState({ kind: "busy" });
        else if (evt.status === "done")
          setMemoryState({ kind: "done", folded: evt.folded });
        else if (evt.status === "failed")
          setMemoryState({ kind: "failed", message: evt.message });
        // Recall arrives before the first token, so a later fold event in the
        // same turn correctly replaces it -- the fold is the bigger news.
        else if (evt.status === "recalled")
          setMemoryState({ kind: "recalled", recalled: evt.recalled });
        else if (evt.status === "retrieval_failed" || evt.status === "index_failed")
          setMemoryState({ kind: "recall_failed", message: evt.message });
      },
      onDone: async () => {
        abortRef.current = null;
        // Swap the draft for the saved reply in one render. Clearing the draft
        // first left a frame with the reply missing: the page shrank, the
        // scroll snapped, then jumped back when the reply reappeared.
        try {
          const fresh = await api.session(sessionId);
          setSession(fresh);
          setStreamText("");
          setBusy(false);
          onDirty?.();
        } catch (e) {
          setStreamText("");
          setBusy(false);
          setError(e.message);
        }
      },
      onError: (e) => {
        setBusy(false);
        setStreamText("");
        setError(e.message);
      },
    });
  }

  /** `speakerId` (group chats) picks who answers; unset lets the server choose. */
  async function send(speakerId) {
    const content = draft.trim();
    if (busy) return;
    if (!content) {
      // Nothing typed: that character simply speaks next.
      if (speakerId) runStream(`/sessions/${sessionId}/respond`, { character_id: speakerId });
      return;
    }
    setDraft("");
    // Optimistic echo so the user's turn appears instantly.
    setSession((s) => ({
      ...s,
      messages: [...s.messages, { id: `tmp-${Date.now()}`, role: "user", content, texted: s.mode === "texting" }],
    }));
    runStream(`/sessions/${sessionId}/messages`, {
      content,
      ...(speakerId ? { speaker_id: speakerId } : {}),
    });
  }

  function regenerate() {
    if (busy || !session?.messages?.length) return;
    setSession((s) => {
      const msgs = [...s.messages];
      if (msgs.at(-1)?.role === "assistant") msgs.pop();
      return { ...s, messages: msgs };
    });
    runStream(`/sessions/${sessionId}/regenerate`, {});
  }

  function stop() {
    abortRef.current?.();
    abortRef.current = null;
    setBusy(false);
    // The server keeps what was written before Stop; give it a moment to
    // save before resyncing, so the partial reply doesn't flicker away.
    setTimeout(() => {
      setStreamText("");
      api.session(sessionId).then(setSession).catch(() => {});
    }, 600);
  }

  if (!sessionId)
    return (
      <div className="flex-1 grid place-items-center text-slate-600 p-6">
        <div className="text-center">
          <p className="text-sm">Pick a character to start a chat.</p>
          <p className="text-xs mt-1">Import a SillyTavern V2 card if the list is empty.</p>
          {/* Without this there is no way to reach the drawer on a phone when
              no chat is open -- the header that normally holds it is not rendered. */}
          <button
            onClick={onOpenNav}
            className="md:hidden mt-4 text-sm px-4 py-2 rounded-lg border border-ink-700 hover:bg-ink-850"
          >
            Browse characters
          </button>
        </div>
      </div>
    );

  return (
    <div className="flex-1 flex flex-col min-w-0">
      <header className="px-3 sm:px-5 py-3 border-b border-ink-800 flex items-center gap-2 sm:gap-3 relative">
        {/* The sidebar is off-canvas below md, so this is the only way back to
            the chat list on a phone. */}
        <button
          onClick={onOpenNav}
          aria-label="Open menu"
          className="md:hidden -ml-1 mr-1 p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-ink-850 shrink-0"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M3 6h18M3 12h18M3 18h18" />
          </svg>
        </button>
        <div className="min-w-0 flex-1">
          {renaming ? (
            <input
              value={titleDraft}
              onChange={(e) => setTitleDraft(e.target.value)}
              onBlur={saveTitle}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveTitle();
                if (e.key === "Escape") setRenaming(false);
              }}
              autoFocus
              className="bg-ink-950 border border-ink-700 rounded px-2 py-0.5 text-sm outline-none focus:border-accent/60"
            />
          ) : (
            <button
              onClick={() => {
                setTitleDraft(session?.title || "");
                setRenaming(true);
              }}
              title="Rename chat"
              className="font-medium truncate max-w-full block hover:text-accent transition text-left"
            >
              {session?.title ?? "…"}
            </button>
          )}
          <div className="text-xs text-slate-500 flex flex-wrap items-center gap-x-1 gap-y-1 mt-0.5">
            as
            <select
              value={session?.persona_id ?? ""}
              onChange={(e) => swapPersona(e.target.value)}
              title="Change who you're playing"
              className="bg-transparent outline-none hover:text-slate-300 cursor-pointer -ml-0.5"
            >
              <option value="">You</option>
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <span className="text-slate-700">·</span>
            {/* Story or texting (app/chat/texting.py): the same chat, as prose or as a
                text thread. The character is told about the switch on the next reply. */}
            <span className="inline-flex rounded-md border border-ink-700 overflow-hidden" role="group">
              {["story", "texting"].map((m) => (
                <button
                  key={m}
                  onClick={() => (session?.mode || "story") !== m && setMode(m)}
                  disabled={busy}
                  className={`px-1.5 ${
                    (session?.mode || "story") === m ? "bg-ink-700 text-slate-200" : "hover:text-slate-300"
                  }`}
                  title={m === "texting" ? "Text each other, like on your phones" : "Scenes in prose"}
                >
                  {m}
                </button>
              ))}
            </span>
            <span className="text-slate-700">·</span>
            <label
              className="flex items-center gap-1 cursor-pointer hover:text-slate-300"
              title={
                session?.autonomous
                  ? session.waiting_because
                    ? `On. Not writing yet: ${session.waiting_because}.`
                    : "On. May write at the next check."
                  : `Let ${charName || "the character"} send messages on their own, with a notification`
              }
            >
              <input
                type="checkbox"
                checked={!!session?.autonomous}
                onChange={(e) => setAutonomous(e.target.checked)}
                className="accent-accent w-3 h-3"
              />
              writes first
            </label>
            {session?.autonomous && (
              <button
                onClick={messageMeNow}
                disabled={busy || nudging}
                className="hover:text-slate-300 disabled:opacity-40"
                title="Have them write to you now, skipping the waiting"
              >
                {nudging ? "writing…" : "· message me now"}
              </button>
            )}
          </div>
        </div>
        {/* Phones: the four actions don't fit beside the title, so they fold
            into a menu. Wider screens keep them as buttons. */}
        <div className="sm:hidden shrink-0 self-start">
          <button
            onClick={() => setMenuOpen((o) => !o)}
            aria-label="Chat actions"
            className="px-2.5 py-1.5 rounded-lg border border-ink-700 text-slate-300 hover:bg-ink-850"
          >
            ⋯
          </button>
          {menuOpen && (
            <>
              <div className="fixed inset-0 z-20" onClick={() => setMenuOpen(false)} />
              <div className="absolute right-3 top-full mt-1 z-30 w-44 rounded-lg border border-ink-700 bg-ink-900 shadow-xl py-1 text-sm">
                {/* Written out rather than mapped over an array of handlers: the
                    hooks linter reads a handler held in a render-time array as a
                    ref access, since regenerate closes over one. */}
                <button
                  disabled={busy}
                  onClick={() => { setMenuOpen(false); regenerate(); }}
                  className="block w-full text-left px-3 py-2 hover:bg-ink-850 disabled:opacity-40"
                >
                  Regenerate
                </button>
                <button
                  onClick={() => { setMenuOpen(false); onOpenMemory?.(); }}
                  className="block w-full text-left px-3 py-2 hover:bg-ink-850"
                >
                  Memory
                </button>
                <button
                  onClick={() => { setMenuOpen(false); onOpenJournal?.(); }}
                  className="block w-full text-left px-3 py-2 hover:bg-ink-850"
                >
                  Journal
                </button>
                <button
                  onClick={() => { setMenuOpen(false); onOpenSettings?.(); }}
                  className="block w-full text-left px-3 py-2 hover:bg-ink-850"
                >
                  Settings
                </button>
              </div>
            </>
          )}
        </div>
        <div className="ml-auto hidden sm:flex items-center gap-2 shrink-0">
          <button
            onClick={regenerate}
            disabled={busy}
            className="text-xs sm:text-sm px-2 sm:px-3 py-1.5 rounded-lg border border-ink-700 hover:bg-ink-850 disabled:opacity-40"
          >
            Regenerate
          </button>
          <button
            onClick={onOpenMemory}
            className="text-xs sm:text-sm px-2 sm:px-3 py-1.5 rounded-lg border border-ink-700 hover:bg-ink-850"
          >
            Memory
          </button>
          <button
            onClick={onOpenJournal}
            title="What the character privately thinks -- never shown in the chat"
            className="text-xs sm:text-sm px-2 sm:px-3 py-1.5 rounded-lg border border-ink-700 hover:bg-ink-850"
          >
            Journal
          </button>
          <button
            onClick={onOpenSettings}
            className="text-xs sm:text-sm px-2 sm:px-3 py-1.5 rounded-lg border border-ink-700 hover:bg-ink-850"
          >
            Settings
          </button>
        </div>
      </header>

      {session?.scene && (
        <SceneBar
          scene={session.scene}
          sessionId={sessionId}
          onSaved={(scene) => setSession((s) => ({ ...s, scene }))}
        />
      )}

      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex-1 overflow-y-auto overflow-x-hidden scroll-thin px-3 sm:px-5 py-6 space-y-5"
      >
        {session?.messages?.map((m, i) => (
          <Message
            key={m.id}
            msg={m}
            swipeable={!busy && i === session.messages.length - 1 && typeof m.id === "number"}
            onRegenerate={regenerate}
            charName={m.speaker_name || charName}
            charId={m.speaker_id || session.character_id}
            userName={userName}
            sessionId={sessionId}
            onChanged={() => {
              reload();
              onDirty?.();
            }}
          />
        ))}

        {streamText && (
          <Message
            msg={{ role: "assistant", content: streamText, texted: session?.mode === "texting" }}
            charName={writerName}
            charId={writerId}
            userName={userName}
            streaming
          />
        )}

        {busy && !streamText && (
          <div className="text-xs text-slate-600 pl-12">
            {writerName} is {session?.mode === "texting" ? "typing" : "writing"}…
          </div>
        )}

        {session?.suspended && (
          <div className="flex justify-center">
            <span
              className="text-[11px] px-3 py-1 rounded-full border border-ink-700 bg-ink-900 text-slate-500"
              title="Settings → Characters writing first"
            >
              {charName} sent {session.unanswered} messages without a reply and is waiting for you
            </span>
          </div>
        )}

        {guardRemoved > 0 && !busy && (
          <div className="flex justify-center">
            <span
              className="text-[11px] px-3 py-1 rounded-full border border-ink-700 bg-ink-900 text-slate-500"
              title="Settings → Keep the character from acting for you"
            >
              removed {guardRemoved} {guardRemoved === 1 ? "line" : "lines"} that acted for you
            </span>
          </div>
        )}

        {memoryState && (
          <div className="flex justify-center">
            <button
              onClick={onOpenMemory}
              className="text-[11px] px-3 py-1 rounded-full border border-ink-700 bg-ink-900 text-slate-500 hover:text-slate-300 hover:border-ink-700"
              title="Open memory panel"
            >
              {memoryState.kind === "busy" && "⋯ condensing earlier turns into memory"}
              {memoryState.kind === "done" &&
                `memory updated — ${memoryState.folded} earlier messages condensed`}
              {memoryState.kind === "failed" &&
                `memory update failed (chat unaffected) — ${memoryState.message}`}
              {memoryState.kind === "recalled" &&
                `recalled ${memoryState.recalled} earlier ${
                  memoryState.recalled === 1 ? "moment" : "moments"
                }`}
              {memoryState.kind === "recall_failed" &&
                `vector recall unavailable (chat unaffected) — ${memoryState.message}`}
            </button>
          </div>
        )}

        {error && (
          <div className="text-sm text-rose-400 bg-rose-950/30 border border-rose-900/50 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      <div className="border-t border-ink-800 p-4">
        {(isGroup || castOpen) && (
          <CastBar
            cast={cast}
            characters={characters}
            ownerId={session?.character_id}
            busy={busy}
            hasDraft={!!draft.trim()}
            open={castOpen}
            setOpen={setCastOpen}
            onAnswer={send}
            onMembers={setMembers}
          />
        )}
        <div className="flex gap-2 items-end">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={2}
            placeholder={
              session?.mode === "texting"
                ? `Text ${charName || "them"}…`
                : "Write your turn…  (Enter to send, Shift+Enter for a new line)"
            }
            className="flex-1 resize-none bg-ink-900 border border-ink-700 rounded-xl px-3 py-2.5 text-sm outline-none focus:border-accent/60 placeholder:text-slate-600"
          />
          {!isGroup && !castOpen && characters.length > 1 && (
            <button
              onClick={() => setCastOpen(true)}
              title="Bring more characters into this scene"
              className="px-3 py-2.5 rounded-xl border border-ink-700 text-sm text-slate-400 hover:bg-ink-850"
            >
              +
            </button>
          )}
          {busy ? (
            <button
              onClick={stop}
              className="px-4 py-2.5 rounded-xl border border-ink-700 text-sm hover:bg-ink-850"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={() => send()}
              disabled={!draft.trim()}
              className="px-4 py-2.5 rounded-xl bg-accent/25 border border-accent/40 text-sm hover:bg-accent/35 disabled:opacity-40"
            >
              Send
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Group chats: who is in the scene, and who answers next. A name button sends
 * the draft to that character, or with nothing typed has them speak next --
 * one character answering another. Send on its own lets the server choose:
 * whoever the message names, else whoever has waited longest.
 */
function CastBar({ cast, characters, ownerId, busy, hasDraft, open, setOpen, onAnswer, onMembers }) {
  const memberIds = cast.filter((c) => c.id !== ownerId).map((c) => c.id);
  const toggle = (id) =>
    onMembers(memberIds.includes(id) ? memberIds.filter((m) => m !== id) : [...memberIds, id]);

  return (
    <div className="mb-2 text-[11px] text-slate-500">
      <div className="flex flex-wrap items-center gap-1.5">
        {cast.length > 1 && <span>{hasDraft ? "Send to" : "Have speak"}</span>}
        {cast.length > 1 &&
          cast.map((c) => (
            <button
              key={c.id}
              onClick={() => onAnswer(c.id)}
              disabled={busy}
              className="px-2.5 py-1 rounded-full border border-ink-700 text-slate-300 hover:border-accent/50 hover:bg-accent/10 disabled:opacity-40"
            >
              {c.name}
            </button>
          ))}
        <button onClick={() => setOpen(!open)} className="ml-auto hover:text-slate-300">
          {open ? "done" : "cast"}
        </button>
      </div>
      {open && (
        <div className="flex flex-wrap items-center gap-1.5 mt-2">
          <span>In the scene</span>
          {characters
            .filter((c) => c.id !== ownerId)
            .map((c) => (
              <button
                key={c.id}
                onClick={() => toggle(c.id)}
                className={`px-2.5 py-1 rounded-full border transition ${
                  memberIds.includes(c.id)
                    ? "border-accent/50 bg-accent/10 text-slate-200"
                    : "border-ink-800 hover:border-ink-700"
                }`}
              >
                {c.name}
              </button>
            ))}
        </div>
      )}
    </div>
  );
}

const SCENE_FIELDS = [
  ["location", "Where"],
  ["time", "When"],
  ["weather", "Weather"],
  ["present", "Present"],
];

/**
 * Where the story is right now. Updated after every reply and fed back to the
 * model before the next one; editable because an 8B sometimes misreads a scene
 * change, and the next update starts from whatever is saved here.
 */
function SceneBar({ scene, sessionId, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(scene);

  useEffect(() => {
    if (!editing) setDraft(scene);
  }, [scene, editing]);

  async function save() {
    try {
      const r = await api.patchScene(sessionId, draft);
      onSaved?.(r.scene);
      setEditing(false);
    } catch {
      /* keep the editor open so nothing typed is lost */
    }
  }

  return (
    <div className="px-5 py-1.5 border-b border-ink-800 bg-ink-900/60 text-[11px] text-slate-500 flex flex-wrap items-center gap-x-4 gap-y-1">
      {SCENE_FIELDS.map(([key, label]) =>
        editing ? (
          <label key={key} className="flex items-center gap-1">
            {label}
            <input
              value={draft[key] ?? ""}
              onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
              maxLength={80}
              className="bg-ink-950 border border-ink-800 rounded px-1.5 py-0.5 text-slate-300 outline-none focus:border-accent/60 w-36"
            />
          </label>
        ) : (
          <span key={key}>
            {label} <span className="text-slate-300">{scene[key] || "unknown"}</span>
          </span>
        )
      )}
      {editing ? (
        <span className="flex gap-2 ml-auto">
          <button onClick={save} className="text-accent hover:underline">save</button>
          <button onClick={() => setEditing(false)} className="hover:text-slate-300">cancel</button>
        </span>
      ) : (
        <button
          onClick={() => setEditing(true)}
          className="ml-auto hover:text-slate-300"
          title="Correct the scene; the next update starts from what you save"
        >
          edit
        </button>
      )}
    </div>
  );
}
