import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import Sidebar from "./components/Sidebar";
import ChatView from "./components/ChatView";
import SettingsPanel from "./components/SettingsPanel";
import MemoryPanel from "./components/MemoryPanel";
import PersonaManager from "./components/PersonaManager";
import WorldManager from "./components/WorldManager";
import NewChatDialog from "./components/NewChatDialog";
import NewWorldChatDialog from "./components/NewWorldChatDialog";
import StatsPanel from "./components/StatsPanel";
import CharacterEditor from "./components/CharacterEditor";
import NewCharacterDialog from "./components/NewCharacterDialog";
import ModelManager from "./components/ModelManager";
import LoginScreen from "./components/LoginScreen";
import JournalPanel from "./components/JournalPanel";

export default function App() {
  const [characters, setCharacters] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [personas, setPersonas] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [health, setHealth] = useState(null);
  // null while unknown -- rendering the app before we know would flash
  // the chat list at someone who is not signed in.
  const [authed, setAuthed] = useState(null);

  const [panel, setPanel] = useState(null); // settings | memory | personas | models
  const [newChatFor, setNewChatFor] = useState(null);
  const [worldChatIn, setWorldChatIn] = useState(null);
  const [editingCharId, setEditingCharId] = useState(null);
  const [creatingChar, setCreatingChar] = useState(false);
  // Drawer state is only meaningful below md; above it the sidebar is static.
  // On a phone with no chat open, the drawer *is* the home screen: starting it
  // closed left an empty "pick a character" page with the list one tap away.
  const [navOpen, setNavOpen] = useState(true);
  useEffect(() => {
    if (activeId === null) setNavOpen(true);
  }, [activeId]);

  const refresh = useCallback(async () => {
    const [c, s, p] = await Promise.all([
      api.characters().catch(() => []),
      api.sessions().catch(() => []),
      api.personas().catch(() => []),
    ]);
    setCharacters(c);
    setSessions(s);
    setPersonas(p);
  }, []);

  const refreshHealth = useCallback(
    () => api.health().then(setHealth).catch(() => setHealth({ ok: false })),
    []
  );

  const checkAuth = useCallback(
    () =>
      api
        .authStatus()
        .then((s) => setAuthed(s.authenticated))
        .catch(() => setAuthed(true)), // status is public; a failure here is not a lockout
    []
  );

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  // A tapped notification opens its chat: /?session=ID on a cold start, or a
  // message from the service worker when the app is already running.
  useEffect(() => {
    const open = (url) => {
      const id = Number(new URL(url, window.location.origin).searchParams.get("session"));
      if (id) {
        setActiveId(id);
        // A tapped notification means that chat, not the drawer sitting over it.
        setNavOpen(false);
      }
    };
    open(window.location.href);
    if (window.location.search) window.history.replaceState(null, "", "/");
    const onMessage = (e) => e.data?.type === "open" && open(e.data.url);
    navigator.serviceWorker?.addEventListener("message", onMessage);
    return () => navigator.serviceWorker?.removeEventListener("message", onMessage);
  }, []);

  useEffect(() => {
    if (!authed) return;
    refresh();
    refreshHealth();
    // Quiet hours and "it is now..." (characters writing first) run on the
    // owner's clock. The browser knows it; keep the server in step, which also
    // follows a move or a trip.
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    api.settings()
      .then((s) => zone && s.timezone !== zone && api.patchSettings({ timezone: zone }))
      .catch(() => {});
  }, [authed, refresh, refreshHealth]);

  async function removeSession(id) {
    if (!confirm("Delete this chat? Cannot be undone.")) return;
    await api.deleteSession(id).catch(() => {});
    if (activeId === id) setActiveId(null);
    refresh();
  }

  // Extras live in their world's panel: the list is who you came to talk to.
  const mainCast = characters.filter((c) => !c.extra);

  if (authed === null) return <div className="h-full bg-ink-950" />;
  if (!authed) return <LoginScreen onSignedIn={() => setAuthed(true)} />;

  return (
    <div className="h-full flex bg-ink-950">
      <Sidebar
        characters={mainCast}
        sessions={sessions}
        activeSessionId={activeId}
        health={health}
        onOpenSession={setActiveId}
        onNewChat={setNewChatFor}
        onEditCharacter={setEditingCharId}
        onNewCharacter={() => setCreatingChar(true)}
        onImported={refresh}
        onDeleteSession={removeSession}
        onManagePersonas={() => setPanel("personas")}
        onManageWorlds={() => setPanel("worlds")}
        onManageModels={() => setPanel("models")}
        onOpenStats={() => setPanel("stats")}
        open={navOpen}
        onClose={() => setNavOpen(false)}
      />

      <ChatView
        onOpenNav={() => setNavOpen(true)}
        sessionId={activeId}
        personas={personas}
        characters={characters}
        onOpenSettings={() => setPanel("settings")}
        onOpenMemory={() => setPanel("memory")}
        onOpenJournal={() => setPanel("journal")}
        onDirty={refresh}
      />

      <SettingsPanel
        open={panel === "settings"}
        onClose={() => setPanel(null)}
        sessionId={activeId}
        onOpenModels={() => setPanel("models")}
      />

      <JournalPanel
        open={panel === "journal"}
        onClose={() => setPanel(null)}
        sessionId={activeId}
      />

      <MemoryPanel
        open={panel === "memory"}
        onClose={() => setPanel(null)}
        sessionId={activeId}
        onChanged={refresh}
        onOpenModels={() => setPanel("models")}
      />

      <PersonaManager
        open={panel === "personas"}
        onClose={() => setPanel(null)}
        onChanged={refresh}
      />

      <WorldManager
        open={panel === "worlds"}
        onClose={() => setPanel(null)}
        onChanged={refresh}
        onChat={(c) => {
          setPanel(null);
          setNewChatFor(c);
        }}
        onWorldChat={(w) => {
          setPanel(null);
          setWorldChatIn(w);
        }}
      />

      <StatsPanel open={panel === "stats"} onClose={() => setPanel(null)} />

      <NewWorldChatDialog
        world={worldChatIn}
        personas={personas}
        onClose={() => setWorldChatIn(null)}
        onCreated={async (id) => {
          await refresh();
          setActiveId(id);
        }}
        onManagePersonas={() => {
          setWorldChatIn(null);
          setPanel("personas");
        }}
      />

      <ModelManager
        open={panel === "models"}
        onClose={() => setPanel(null)}
        onChanged={refreshHealth}
      />

      <NewChatDialog
        open={newChatFor !== null}
        character={newChatFor}
        characters={mainCast}
        personas={personas}
        onClose={() => setNewChatFor(null)}
        onCreated={async (id) => {
          await refresh();
          setActiveId(id);
        }}
        onManagePersonas={() => {
          setNewChatFor(null);
          setPanel("personas");
        }}
      />

      <NewCharacterDialog
        open={creatingChar}
        onClose={() => setCreatingChar(false)}
        onCreated={async (id) => {
          await refresh();
          // Straight into the editor: the dialog asks for the three fields that
          // make a character playable, not the ten that make one finished.
          setEditingCharId(id);
        }}
      />

      <CharacterEditor
        open={editingCharId !== null}
        characterId={editingCharId}
        onClose={() => setEditingCharId(null)}
        onChanged={refresh}
      />
    </div>
  );
}
