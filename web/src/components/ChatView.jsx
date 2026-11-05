import { useEffect, useRef, useState } from "react";
import { api, streamTurn, stripSpeakerPrefix } from "../api";
import Message from "./Message";
import PersonDrafter from "./PersonDrafter";
import { play, speechPath, stopPlaying } from "../player";

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
  // Below lg the scene panel slides over; from lg up it's always beside the story.
  const [panelOpen, setPanelOpen] = useState(false);
  const abortRef = useRef(null);
  const lastRead = useRef(null);
  const composerRef = useRef(null);
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

  // Speaking instead of typing. The words land in the box to check before sending. Browsers
  // only offer the microphone over HTTPS, so a plain LAN address can't use it.
  const [mic, setMic] = useState(null); // null | "recording" | "hearing"
  const recorder = useRef(null);
  const micTimer = useRef(null);
  async function startListening() {
    setError(null);
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("This browser can't use the microphone here. It needs an HTTPS address, like your public one.");
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("The microphone is blocked. Allow it for this site in your browser, then try again.");
      return;
    }
    const rec = new MediaRecorder(stream);
    const chunks = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = async () => {
      clearTimeout(micTimer.current);
      stream.getTracks().forEach((t) => t.stop());
      recorder.current = null;
      setMic("hearing");
      try {
        const text = await api.transcribe(sessionId, new Blob(chunks, { type: rec.mimeType }));
        if (text) setDraft((d) => (d.trim() ? `${d.trimEnd()} ${text}` : text));
        else setError("Didn't catch anything. Try again a little closer to the mic.");
      } catch (e) {
        setError(e.message);
      } finally {
        setMic(null);
      }
    };
    recorder.current = rec;
    rec.start();
    setMic("recording");
    // A forgotten mic shouldn't record all night.
    micTimer.current = setTimeout(() => recorder.current?.stop(), 2 * 60 * 1000);
  }
  // Leaving the chat stops the microphone.
  useEffect(() => () => {
    clearTimeout(micTimer.current);
    const rec = recorder.current;
    if (rec) {
      rec.onstop = null;
      rec.stream.getTracks().forEach((t) => t.stop());
      rec.stop();
      recorder.current = null;
    }
  }, [sessionId]);

  async function setChoiceMode(on) {
    try {
      await api.updateSession(sessionId, { choice_mode: on });
      await reload();
    } catch (e) {
      setError(e.message);
    }
  }

  async function setNarrator(on) {
    try {
      await api.updateSession(sessionId, { narrator: on });
      await reload();
    } catch (e) {
      setError(e.message);
    }
  }

  // "Something happens": the narrator posts one small event, then the character
  // answers it through the usual stream.
  const [narrating, setNarrating] = useState(false);
  async function somethingHappens() {
    setNarrating(true);
    setError(null);
    try {
      const line = await api.narrate(sessionId);
      setSession((s) => ({ ...s, messages: [...s.messages, line] }));
      runStream(`/sessions/${sessionId}/respond`, {});
    } catch (e) {
      setError(e.message);
    } finally {
      setNarrating(false);
    }
  }

  // "Someone arrives": a person of this world joins the scene, announced by the
  // narrator, and speaks first. The list is whoever of the world isn't here yet.
  const [arrivals, setArrivals] = useState(null); // null | "list" | "new"
  const [arriving, setArriving] = useState(false);
  async function bringIn(person) {
    setArrivals(null);
    setArriving(true);
    setError(null);
    try {
      const r = await api.bringGuest(sessionId, person.id);
      setSession((s) => ({ ...s, cast: r.cast, messages: [...s.messages, r.message] }));
      onDirty?.();
      runStream(`/sessions/${sessionId}/respond`, { character_id: person.id });
    } catch (e) {
      setError(e.message);
    } finally {
      setArriving(false);
    }
  }

  // Remembered per device: it's about where you are, not about the chat.
  const [readAloud, setReadAloud] = useState(() => {
    try {
      return localStorage.getItem("readAloud") === "1";
    } catch {
      return false;
    }
  });
  function chooseReadAloud(on) {
    setReadAloud(on);
    if (!on) stopPlaying();
    try {
      localStorage.setItem("readAloud", on ? "1" : "0");
    } catch {
      /* private window: it just won't be remembered */
    }
  }

  async function exportChat() {
    try {
      await api.exportChat(sessionId);
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
  // The box grows with what's typed, up to its max height.
  useEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [draft]);
  // A newly opened chat starts at its latest message.
  useEffect(() => {
    stickRef.current = true;
  }, [sessionId]);

  // Choose your own adventure: asked for once the reply is in, so it never holds one up,
  // and dropped if the chat has moved on by the time they arrive.
  const [choices, setChoices] = useState([]);
  const [choosing, setChoosing] = useState(false);
  // Three long options can take a third of a phone screen. Hidden until the next reply.
  const [choicesHidden, setChoicesHidden] = useState(false);
  const lastMessage = session?.messages?.[session.messages.length - 1];
  const choiceKey =
    session?.choice_mode && !busy && lastMessage && lastMessage.role !== "user"
      ? `${sessionId}:${lastMessage.id}:${lastMessage.content}`
      : null;
  const choiceRun = useRef(0);
  async function fetchChoices(refresh) {
    const run = ++choiceRun.current;
    setChoices([]);
    setChoosing(true);
    try {
      const r = await api.choices(sessionId, refresh);
      if (run === choiceRun.current) setChoices(r.choices);
    } catch (e) {
      if (run === choiceRun.current) setError(e.message);
    } finally {
      if (run === choiceRun.current) setChoosing(false);
    }
  }
  useEffect(() => {
    setChoicesHidden(false);
    if (choiceKey) {
      fetchChoices(false);
    } else {
      choiceRun.current++;
      setChoices([]);
      setChoosing(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [choiceKey]);

  const charName = session?.character_name;
  const cast = session?.cast || [];
  const isGroup = cast.length > 1;
  const writerName = streamSpeaker?.name || charName;

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
        // Posted before the reply starts, so it belongs above the draft.
        if (evt.type === "narrator")
          setSession((s) =>
            s ? { ...s, messages: [...s.messages, { id: evt.id, role: "narrator", content: evt.content }] } : s
          );
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
          const last = fresh.messages[fresh.messages.length - 1];
          // onDone can fire twice for one reply (streamTurn), which restarted the reading.
          // By words as well as id, so a swipe of the same message is read too.
          const heard = last && `${last.id}:${last.content}`;
          if (readAloud && last?.role === "assistant" && lastRead.current !== heard) {
            lastRead.current = heard;
            play(speechPath(sessionId, last.id));
          }
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
  async function send(speakerId, chosen) {
    const content = (chosen ?? draft).trim();
    if (busy) return;
    if (!content) {
      // Nothing typed: that character simply speaks next.
      if (speakerId) runStream(`/sessions/${sessionId}/respond`, { character_id: speakerId });
      return;
    }
    // A choice leaves anything half typed where it is.
    if (chosen === undefined) setDraft("");
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
      <div className="flex-1 grid place-items-center text-slate-500 p-6">
        <div className="text-center">
          <p className="font-serif text-xl text-slate-300">Pick up a story, or start a new one.</p>
          <p className="text-sm mt-2">Your chats and characters are in the sidebar.</p>
          {/* Without this there is no way to reach the drawer on a phone when
              no chat is open -- the header that normally holds it is not rendered. */}
          <button
            onClick={onOpenNav}
            className="md:hidden mt-4 text-sm min-h-[44px] px-4 rounded-lg border border-ink-700 hover:bg-ink-850"
          >
            Open chats and people
          </button>
        </div>
      </div>
    );

  const scene = session?.scene;
  const sceneLine = scene
    ? [scene.location, scene.time, scene.weather]
        .filter((v) => v && v !== "unknown" && v.toLowerCase() !== "indoors")
        .join(" · ")
    : "";

  return (
    <div className="flex-1 flex min-w-0">
      <div className="flex-1 flex flex-col min-w-0">
        {/* Just who and where. Everything else about the chat lives in the scene panel. */}
        <header className="flex items-center gap-1 px-2 py-2 border-b border-ink-800 lg:justify-center">
          {/* The sidebar is off-canvas below md, so this is the only way back to
              the chat list on a phone. */}
          <IconButton label="Open chats" onClick={onOpenNav} className="md:hidden">
            <path d="M4 7h16M4 12h16M4 17h16" />
          </IconButton>
          <div className="flex-1 min-w-0 text-center py-1">
            <div className="font-serif text-[17px] font-semibold truncate">
              {cast.length > 1 ? cast.map((c) => c.name).join(" & ") : charName ?? "…"}
            </div>
            {sceneLine && <div className="text-xs text-slate-500 truncate">{sceneLine}</div>}
          </div>
          <IconButton label="Scene options" onClick={() => setPanelOpen(true)} className="lg:hidden">
            <path d="M4 8h10M18 8h2M4 16h2M10 16h10" />
            <circle cx="16" cy="8" r="2" />
            <circle cx="8" cy="16" r="2" />
          </IconButton>
        </header>

        <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto overflow-x-hidden scroll-thin">
          <div className="max-w-[640px] mx-auto px-6 sm:px-8 py-6 sm:py-10 space-y-5">
            {session?.messages?.map((m, i) => (
              <Message
                key={m.id}
                msg={m}
                swipeable={!busy && i === session.messages.length - 1 && typeof m.id === "number"}
                onRegenerate={regenerate}
                charName={m.speaker_name || charName}
                showName={isGroup}
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
                showName={isGroup}
                userName={userName}
                streaming
              />
            )}

            {busy && !streamText && (
              <p className="text-sm text-slate-500">
                {writerName} is {session?.mode === "texting" ? "typing" : "writing"}…
              </p>
            )}

            {session?.suspended && (
              <p className="text-xs text-center text-slate-500" title="Settings → Characters writing first">
                {charName} sent {session.unanswered} messages without a reply and is waiting for you
              </p>
            )}

            {guardRemoved > 0 && !busy && (
              <p className="text-xs text-center text-slate-500" title="Settings → Keep the character from acting for you">
                removed {guardRemoved} {guardRemoved === 1 ? "line" : "lines"} that acted for you
              </p>
            )}

            {memoryState && (
              <p className="text-center">
                <button onClick={onOpenMemory} className="text-xs text-slate-500 hover:text-slate-300" title="Open memory panel">
                  {memoryState.kind === "busy" && "condensing earlier turns into memory…"}
                  {memoryState.kind === "done" &&
                    `memory updated: ${memoryState.folded} earlier messages condensed`}
                  {memoryState.kind === "failed" &&
                    `memory update failed, chat unaffected: ${memoryState.message}`}
                  {memoryState.kind === "recalled" &&
                    `recalled ${memoryState.recalled} earlier ${
                      memoryState.recalled === 1 ? "moment" : "moments"
                    }`}
                  {memoryState.kind === "recall_failed" &&
                    `recall unavailable, chat unaffected: ${memoryState.message}`}
                </button>
              </p>
            )}

            {error && (
              <div className="text-sm text-rose-300 border-l-2 border-rose-400 pl-3 py-1">{error}</div>
            )}

            <div ref={bottomRef} />
          </div>
        </div>

        <div className="border-t border-ink-800">
          <div className="max-w-[640px] mx-auto px-3 sm:px-8 pt-1 pb-3 sm:pb-5">
            {session?.choice_mode && !busy && (choosing || choices.length > 0) && (
              <div className="mb-2 text-sm">
                {choosing && <p className="text-xs text-slate-500 py-2">Thinking of choices…</p>}
                {!choicesHidden && choices.map((c, i) => (
                  <button
                    key={i}
                    onClick={() => send(undefined, c)}
                    className={`block w-full min-h-[44px] text-left px-1 py-2 text-slate-200 hover:text-accent break-words ${
                      i ? "border-t border-ink-800" : ""
                    }`}
                  >
                    {c.replace(/\*/g, "")}
                  </button>
                ))}
                {!choosing && (
                  <div className="flex gap-4 px-1 text-xs text-slate-500">
                    {!choicesHidden && (
                      <button onClick={() => fetchChoices(true)} className="py-1 hover:text-slate-300">
                        other choices
                      </button>
                    )}
                    <button onClick={() => setChoicesHidden((h) => !h)} className="py-1 hover:text-slate-300">
                      {choicesHidden ? "show choices" : "hide"}
                    </button>
                  </div>
                )}
              </div>
            )}
            {arrivals === "list" && (
              <div className="py-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
                <span className="text-xs text-slate-500">Who arrives?</span>
                {characters
                  .filter((c) => c.world_id === session?.world_id && !cast.some((m) => m.id === c.id))
                  .sort((a, b) => Number(b.extra) - Number(a.extra))
                  .map((c) => (
                    <button key={c.id} onClick={() => bringIn(c)} className="text-slate-200 hover:text-accent">
                      {c.name}
                    </button>
                  ))}
                <button onClick={() => setArrivals("new")} className="text-accent hover:underline">
                  someone new
                </button>
                <button onClick={() => setArrivals(null)} className="ml-auto text-xs text-slate-500 hover:text-slate-300">
                  close
                </button>
              </div>
            )}
            {arrivals === "new" && (
              <div className="my-2 max-h-[60vh] overflow-y-auto scroll-thin border border-ink-800 rounded-lg p-3">
                <PersonDrafter
                  worldId={session?.world_id}
                  inChat
                  onKept={(person, how) => {
                    onDirty?.();
                    if (how === "guest") bringIn(person);
                    else setArrivals(null);
                  }}
                  onCancel={() => setArrivals(null)}
                />
              </div>
            )}
            {isGroup && (
              <div className="py-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                <span className="text-xs text-slate-500">{draft.trim() ? "Send to" : "Have speak"}</span>
                {cast.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => send(c.id)}
                    disabled={busy}
                    className="min-h-[32px] text-slate-200 hover:text-accent disabled:opacity-40"
                  >
                    {c.name}
                  </button>
                ))}
              </div>
            )}
            <div className="flex gap-1.5 items-end pt-2">
              <textarea
                ref={composerRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                rows={1}
                aria-label="Your turn"
                placeholder={session?.mode === "texting" ? `Text ${charName || "them"}…` : "Your turn…"}
                title="Enter to send, Shift+Enter for a new line"
                className="flex-1 min-w-0 min-h-[44px] max-h-40 resize-none bg-ink-850 border border-ink-700 rounded-lg px-3 py-2.5 text-[15px] leading-snug outline-none focus:border-accent/60 placeholder:text-slate-500"
              />
              <button
                onClick={() => (mic === "recording" ? recorder.current?.stop() : startListening())}
                disabled={mic === "hearing"}
                title={mic === "recording" ? "Stop and turn this into text" : "Speak instead of typing"}
                aria-label={mic === "recording" ? "Stop recording" : "Speak instead of typing"}
                className={`w-11 h-11 shrink-0 grid place-items-center rounded-lg disabled:opacity-40 ${
                  mic === "recording" ? "bg-rose-500/25 text-rose-200" : "text-slate-400 hover:text-slate-200 hover:bg-ink-850"
                }`}
              >
                {mic === "hearing" ? (
                  <span aria-hidden="true">…</span>
                ) : (
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
                    <rect x="9" y="3" width="6" height="12" rx="3" />
                    <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
                  </svg>
                )}
              </button>
              {busy ? (
                <button
                  onClick={stop}
                  aria-label="Stop writing"
                  title="Stop"
                  className="w-11 h-11 shrink-0 grid place-items-center rounded-lg border border-ink-700 text-slate-200 hover:bg-ink-850"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <rect x="5" y="5" width="14" height="14" rx="2" />
                  </svg>
                </button>
              ) : (
                <button
                  onClick={() => send()}
                  disabled={!draft.trim()}
                  aria-label="Send"
                  title="Send"
                  className="w-11 h-11 shrink-0 grid place-items-center rounded-lg bg-accent text-ink-950 disabled:opacity-40"
                >
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                    <path d="M5 12h14M13 6l6 6-6 6" />
                  </svg>
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {panelOpen && (
        <div className="lg:hidden fixed inset-0 z-30 bg-black/60" onClick={() => setPanelOpen(false)} aria-hidden="true" />
      )}
      <aside
        aria-label="Scene"
        className={`bg-ink-950 border-l border-ink-800 w-72 lg:w-64 shrink-0 overflow-y-auto scroll-thin
          fixed inset-y-0 right-0 z-40 transition-transform duration-200
          ${panelOpen ? "translate-x-0" : "translate-x-full"}
          lg:static lg:translate-x-0 lg:z-auto`}
      >
        <div className="p-5 space-y-5 text-sm">
          <div className="flex items-start gap-2">
            <div className="flex-1 min-w-0">
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
                  aria-label="Chat title"
                  className="w-full bg-ink-850 border border-ink-700 rounded px-2 py-1 outline-none focus:border-accent/60"
                />
              ) : (
                <button
                  onClick={() => {
                    setTitleDraft(session?.title || "");
                    setRenaming(true);
                  }}
                  title="Rename chat"
                  className="font-serif text-[17px] font-semibold text-left hover:text-accent break-words max-w-full"
                >
                  {session?.title ?? "…"}
                </button>
              )}
            </div>
            <IconButton label="Close scene options" onClick={() => setPanelOpen(false)} className="lg:hidden -mt-2 -mr-2">
              <path d="M6 6l12 12M18 6L6 18" />
            </IconButton>
          </div>

          {scene && (
            <SceneDetails
              scene={scene}
              sessionId={sessionId}
              onSaved={(s) => setSession((old) => ({ ...old, scene: s }))}
            />
          )}

          <PanelSection>
            {(session?.mode || "story") === "story" && (
              <Toggle
                label="Narrator"
                hint="A short line about the world now and then"
                checked={!!session?.narrator}
                onChange={setNarrator}
              />
            )}
            <Toggle
              label="Choices"
              hint="Three things you could do next, above the type box"
              checked={!!session?.choice_mode}
              onChange={setChoiceMode}
            />
            <Toggle label="Read aloud" hint="Each new reply, on this device" checked={readAloud} onChange={chooseReadAloud} />
            <Toggle
              label="Writes first"
              hint={
                session?.autonomous
                  ? session.waiting_because
                    ? `On. Not writing yet: ${session.waiting_because}.`
                    : "On. May write at the next check."
                  : `${charName || "They"} may message you on their own`
              }
              checked={!!session?.autonomous}
              onChange={setAutonomous}
            />
            {session?.autonomous && (
              <button onClick={messageMeNow} disabled={busy || nudging} className="block text-accent hover:underline disabled:opacity-40">
                {nudging ? "Writing…" : "Message me now"}
              </button>
            )}
          </PanelSection>

          <PanelSection>
            {/* Story or texting (app/chat/texting.py): the same chat, as prose or as a
                text thread. The character is told about the switch on the next reply. */}
            <div className="flex rounded-lg border border-ink-700 overflow-hidden" role="group" aria-label="Chat style">
              {["story", "texting"].map((m) => (
                <button
                  key={m}
                  onClick={() => (session?.mode || "story") !== m && setMode(m)}
                  disabled={busy}
                  aria-pressed={(session?.mode || "story") === m}
                  className={`flex-1 min-h-[38px] capitalize ${
                    (session?.mode || "story") === m ? "bg-ink-800 text-slate-100" : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {m}
                </button>
              ))}
            </div>
            <label className="block text-xs text-slate-500">
              Playing as
              <select
                value={session?.persona_id ?? ""}
                onChange={(e) => swapPersona(e.target.value)}
                className="mt-1 w-full bg-ink-850 border border-ink-700 rounded-lg px-2 py-2 text-sm text-slate-200 outline-none focus:border-accent/60"
              >
                <option value="">You</option>
                {personas.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          </PanelSection>

          <PanelSection>
            {(session?.mode || "story") === "story" && (
              <PanelLink
                disabled={busy || narrating}
                title="The narrator brings in something small, and they respond to it"
                onClick={() => {
                  setPanelOpen(false);
                  somethingHappens();
                }}
              >
                {narrating ? "Thinking…" : "Something happens"}
              </PanelLink>
            )}
            {(session?.mode || "story") === "story" && session?.world_id && (
              <PanelLink
                disabled={busy || arriving}
                title="Someone from this world joins the scene"
                onClick={() => {
                  setPanelOpen(false);
                  setArrivals(arrivals ? null : "list");
                }}
              >
                {arriving ? "On their way…" : "Someone arrives"}
              </PanelLink>
            )}
            <PanelLink
              disabled={busy}
              onClick={() => {
                setPanelOpen(false);
                regenerate();
              }}
            >
              Regenerate
            </PanelLink>
          </PanelSection>

          <PanelSection>
            <PanelLink
              onClick={() => {
                setPanelOpen(false);
                onOpenMemory?.();
              }}
            >
              Memory
            </PanelLink>
            <PanelLink
              title="What the character privately thinks, never shown in the chat"
              onClick={() => {
                setPanelOpen(false);
                onOpenJournal?.();
              }}
            >
              Journal
            </PanelLink>
            <PanelLink title="Save this chat as a file, to keep or import on another install" onClick={exportChat}>
              Export
            </PanelLink>
            <PanelLink
              onClick={() => {
                setPanelOpen(false);
                onOpenSettings?.();
              }}
            >
              Settings
            </PanelLink>
          </PanelSection>

          <PanelSection title="In the scene">
            <div className="flex flex-wrap gap-1.5 text-xs">
              {characters
                .filter((c) => c.id !== session?.character_id && (!c.extra || cast.some((m) => m.id === c.id)))
                .map((c) => {
                  const here = cast.some((m) => m.id === c.id);
                  const others = cast.filter((m) => m.id !== session?.character_id).map((m) => m.id);
                  return (
                    <button
                      key={c.id}
                      aria-pressed={here}
                      onClick={() => setMembers(here ? others.filter((id) => id !== c.id) : [...others, c.id])}
                      className={`px-2.5 py-1.5 rounded-md border ${
                        here ? "border-accent/60 text-accent" : "border-ink-700 text-slate-400 hover:text-slate-200"
                      }`}
                    >
                      {c.name}
                    </button>
                  );
                })}
            </div>
          </PanelSection>
        </div>
      </aside>
    </div>
  );
}

function IconButton({ label, onClick, className = "", children }) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      title={label}
      className={`w-11 h-11 shrink-0 grid place-items-center rounded-lg text-slate-400 hover:text-slate-200 ${className}`}
    >
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
        {children}
      </svg>
    </button>
  );
}

function PanelSection({ title, children }) {
  return (
    <div className="border-t border-ink-800 pt-4 space-y-3">
      {title && <div className="text-xs text-slate-500">{title}</div>}
      {children}
    </div>
  );
}

function PanelLink({ children, ...props }) {
  return (
    <button {...props} className="block w-full min-h-[28px] text-left text-slate-300 hover:text-accent disabled:opacity-40">
      {children}
    </button>
  );
}

function Toggle({ label, hint, checked, onChange }) {
  return (
    <label className="flex gap-2.5 items-start cursor-pointer">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="accent-accent w-4 h-4 mt-0.5 shrink-0"
      />
      <span className="min-w-0">
        <span className="block text-slate-200">{label}</span>
        <span className="block text-xs text-slate-500 leading-snug mt-0.5">{hint}</span>
      </span>
    </label>
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
function SceneDetails({ scene, sessionId, onSaved }) {
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
    <div className="border-t border-ink-800 pt-4 space-y-1.5 text-xs">
      {SCENE_FIELDS.map(([key, label]) =>
        editing ? (
          <label key={key} className="block text-slate-500">
            {label}
            <input
              value={draft[key] ?? ""}
              onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
              maxLength={80}
              className="mt-0.5 w-full bg-ink-850 border border-ink-700 rounded px-2 py-1 text-slate-200 outline-none focus:border-accent/60"
            />
          </label>
        ) : (
          <div key={key} className="flex gap-2">
            <span className="w-14 shrink-0 text-slate-500">{label}</span>
            <span className="min-w-0 text-slate-300 break-words">{scene[key] || "unknown"}</span>
          </div>
        )
      )}
      {editing ? (
        <div className="flex gap-3 pt-1">
          <button onClick={save} className="text-accent hover:underline">Save</button>
          <button onClick={() => setEditing(false)} className="text-slate-400 hover:text-slate-200">Cancel</button>
        </div>
      ) : (
        <button
          onClick={() => setEditing(true)}
          className="text-slate-500 hover:text-slate-300"
          title="Correct the scene; the next update starts from what you save"
        >
          Edit
        </button>
      )}
    </div>
  );
}
