import { useEffect, useRef, useState } from "react";
import { api, streamTurn, stripSpeakerPrefix } from "../api";
import Message from "./Message";

export default function ChatView({
  sessionId,
  personas,
  onOpenSettings,
  onOpenMemory,
  onDirty,
}) {
  const [session, setSession] = useState(null);
  const [draft, setDraft] = useState("");
  const [streamText, setStreamText] = useState("");
  const [busy, setBusy] = useState(false);
  const [memoryState, setMemoryState] = useState(null);
  const [error, setError] = useState(null);
  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
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

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [session?.messages?.length, streamText]);

  const charName = session?.character_name;
  const userName = personas.find((p) => p.id === session?.persona_id)?.name || "You";

  function runStream(path, body) {
    setBusy(true);
    setError(null);
    setStreamText("");
    let acc = "";

    abortRef.current = streamTurn(path, body, {
      onToken: (t) => {
        acc += t;
        setStreamText(stripSpeakerPrefix(acc, charName));
      },
      onMemory: (evt) => {
        if (evt.status === "compressing") setMemoryState({ kind: "busy" });
        else if (evt.status === "done")
          setMemoryState({ kind: "done", folded: evt.folded });
        else if (evt.status === "failed")
          setMemoryState({ kind: "failed", message: evt.message });
      },
      onDone: async () => {
        setBusy(false);
        setStreamText("");
        abortRef.current = null;
        try {
          setSession(await api.session(sessionId));
          onDirty?.();
        } catch (e) {
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

  async function send() {
    const content = draft.trim();
    if (!content || busy) return;
    setDraft("");
    // Optimistic echo so the user's turn appears instantly.
    setSession((s) => ({
      ...s,
      messages: [...s.messages, { id: `tmp-${Date.now()}`, role: "user", content }],
    }));
    runStream(`/sessions/${sessionId}/messages`, { content });
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
    setStreamText("");
    // The server finished its turn regardless; resync to pick up what persisted.
    api.session(sessionId).then(setSession).catch(() => {});
  }

  if (!sessionId)
    return (
      <div className="flex-1 grid place-items-center text-slate-600">
        <div className="text-center">
          <p className="text-sm">Pick a character to start a chat.</p>
          <p className="text-xs mt-1">Import a SillyTavern V2 card if the list is empty.</p>
        </div>
      </div>
    );

  return (
    <div className="flex-1 flex flex-col min-w-0">
      <header className="px-5 py-3 border-b border-ink-800 flex items-center gap-3">
        <div className="min-w-0">
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
              className="font-medium truncate hover:text-accent transition text-left"
            >
              {session?.title ?? "…"}
            </button>
          )}
          <div className="text-xs text-slate-500 flex items-center gap-1">
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
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={regenerate}
            disabled={busy}
            className="text-sm px-3 py-1.5 rounded-lg border border-ink-700 hover:bg-ink-850 disabled:opacity-40"
          >
            Regenerate
          </button>
          <button
            onClick={onOpenMemory}
            className="text-sm px-3 py-1.5 rounded-lg border border-ink-700 hover:bg-ink-850"
          >
            Memory
          </button>
          <button
            onClick={onOpenSettings}
            className="text-sm px-3 py-1.5 rounded-lg border border-ink-700 hover:bg-ink-850"
          >
            Settings
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto scroll-thin px-5 py-6 space-y-5">
        {session?.messages?.map((m) => (
          <Message
            key={m.id}
            msg={m}
            charName={charName}
            charId={session.character_id}
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
            msg={{ role: "assistant", content: streamText }}
            charName={charName}
            charId={session?.character_id}
            userName={userName}
            streaming
          />
        )}

        {busy && !streamText && (
          <div className="text-xs text-slate-600 pl-12">{charName} is writing…</div>
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
            placeholder="Write your turn…  (Enter to send, Shift+Enter for a new line)"
            className="flex-1 resize-none bg-ink-900 border border-ink-700 rounded-xl px-3 py-2.5 text-sm outline-none focus:border-accent/60 placeholder:text-slate-600"
          />
          {busy ? (
            <button
              onClick={stop}
              className="px-4 py-2.5 rounded-xl border border-ink-700 text-sm hover:bg-ink-850"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={send}
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
