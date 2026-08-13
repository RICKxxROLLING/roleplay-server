import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import Sidebar from "./components/Sidebar";
import ChatView from "./components/ChatView";
import SettingsPanel from "./components/SettingsPanel";
import MemoryPanel from "./components/MemoryPanel";
import PersonaManager from "./components/PersonaManager";
import NewChatDialog from "./components/NewChatDialog";
import CharacterEditor from "./components/CharacterEditor";
import NewCharacterDialog from "./components/NewCharacterDialog";
import ModelManager from "./components/ModelManager";
import LoginScreen from "./components/LoginScreen";

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
  const [editingCharId, setEditingCharId] = useState(null);
  const [creatingChar, setCreatingChar] = useState(false);

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

  useEffect(() => {
    if (!authed) return;
    refresh();
    refreshHealth();
  }, [authed, refresh, refreshHealth]);

  async function removeSession(id) {
    if (!confirm("Delete this chat? Cannot be undone.")) return;
    await api.deleteSession(id).catch(() => {});
    if (activeId === id) setActiveId(null);
    refresh();
  }

  if (authed === null) return <div className="h-full bg-ink-950" />;
  if (!authed) return <LoginScreen onSignedIn={() => setAuthed(true)} />;

  return (
    <div className="h-full flex bg-ink-950">
      <Sidebar
        characters={characters}
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
        onManageModels={() => setPanel("models")}
      />

      <ChatView
        sessionId={activeId}
        personas={personas}
        onOpenSettings={() => setPanel("settings")}
        onOpenMemory={() => setPanel("memory")}
        onDirty={refresh}
      />

      <SettingsPanel
        open={panel === "settings"}
        onClose={() => setPanel(null)}
        sessionId={activeId}
        onOpenModels={() => setPanel("models")}
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

      <ModelManager
        open={panel === "models"}
        onClose={() => setPanel(null)}
        onChanged={refreshHealth}
      />

      <NewChatDialog
        open={newChatFor !== null}
        character={newChatFor}
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
