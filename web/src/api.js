// Same-origin by default: the container serves this bundle from the API host.
export const API = import.meta.env.VITE_API_URL ?? "/api";

async function j(path, opts = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  health: () => j("/health"),
  settings: () => j("/settings"),
  patchSettings: (body) =>
    j("/settings", { method: "PATCH", body: JSON.stringify(body) }),

  models: () => j("/models"),

  characters: () => j("/characters"),
  character: (id) => j(`/characters/${id}`),
  updateCharacter: (id, patch) =>
    j(`/characters/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteCharacter: (id) => j(`/characters/${id}`, { method: "DELETE" }),
  avatarUrl: (id) => `${API}/characters/${id}/avatar`,
  importCard: async (file) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${API}/characters/import`, {
      method: "POST",
      body: fd,
    });
    if (!res.ok) throw new Error((await res.json()).detail ?? "Import failed");
    return res.json();
  },

  personas: () => j("/personas"),
  createPersona: (body) =>
    j("/personas", { method: "POST", body: JSON.stringify(body) }),
  updatePersona: (id, patch) =>
    j(`/personas/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deletePersona: (id) => j(`/personas/${id}`, { method: "DELETE" }),

  sessions: () => j("/sessions"),
  session: (id) => j(`/sessions/${id}`),
  createSession: (body) =>
    j("/sessions", { method: "POST", body: JSON.stringify(body) }),
  updateSession: (id, patch) =>
    j(`/sessions/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteSession: (id) => j(`/sessions/${id}`, { method: "DELETE" }),
  inspectPrompt: (id) => j(`/sessions/${id}/prompt`),

  editMessage: (sid, mid, content) =>
    j(`/sessions/${sid}/messages/${mid}`, {
      method: "PATCH",
      body: JSON.stringify({ content }),
    }),
  deleteMessage: (sid, mid) =>
    j(`/sessions/${sid}/messages/${mid}`, { method: "DELETE" }),

  memory: (id) => j(`/sessions/${id}/memory`),
  saveMemory: (id, summary) =>
    j(`/sessions/${id}/memory`, {
      method: "PATCH",
      body: JSON.stringify({ summary }),
    }),
  forceSummarize: (id) => j(`/sessions/${id}/summarize`, { method: "POST" }),
  reindex: (id) => j(`/sessions/${id}/reindex`, { method: "POST" }),
};

/**
 * Stream a reply over SSE.
 *
 * EventSource can't issue POSTs, so we read the response body manually.
 * Returns an abort function.
 */
export function streamTurn(
  path,
  body,
  { onToken, onMemory, onDone, onError, onEvent }
) {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await fetch(`${API}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body ?? {}),
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line.
        const frames = buf.split("\n\n");
        buf = frames.pop() ?? "";

        for (const frame of frames) {
          const line = frame.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          const evt = JSON.parse(line.slice(6));
          // Catch-all first, so callers can handle event types this helper
          // doesn't know about (model-pull progress, for example).
          onEvent?.(evt);
          if (evt.type === "token") onToken?.(evt.text);
          else if (evt.type === "memory") onMemory?.(evt);
          else if (evt.type === "done") onDone?.();
          else if (evt.type === "error") onError?.(new Error(evt.message));
        }
      }
      onDone?.();
    } catch (err) {
      if (err.name !== "AbortError") onError?.(err);
    }
  })();

  return () => controller.abort();
}

/**
 * Models often echo "CharName:" despite the prompt priming it away.
 * The server strips this before persisting, so the live stream must match --
 * otherwise the prefix visibly vanishes on reload.
 */
export function stripSpeakerPrefix(text, name) {
  if (!name) return text;
  const re = new RegExp(`^\\s*${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*:\\s*`);
  return text.replace(re, "");
}
