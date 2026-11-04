// One voice at a time across the whole app. `play` has to start inside the tap that asked
// for it: phones refuse audio started later, so the URL goes straight to the element rather
// than being fetched first.
import { API } from "./api";

let audio = null;
let current = null;
let listeners = new Set();

const announce = () => listeners.forEach((fn) => fn(current));

export function onPlayerChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function stopPlaying() {
  if (audio) audio.pause();
  audio = null;
  current = null;
  announce();
}

export function isPlaying(path) {
  return current === path;
}

export function play(path, onError) {
  stopPlaying();
  const el = new Audio(`${API}${path}`);
  audio = el;
  current = path;
  announce();
  const done = () => {
    if (audio === el) stopPlaying();
  };
  el.onended = done;
  el.onerror = async () => {
    if (audio !== el) return;
    done();
    // The element only says it failed. Ask again for the server's reason.
    let why = "Couldn't play that.";
    try {
      const res = await fetch(`${API}${path}`);
      if (!res.ok) why = (await res.json()).detail ?? why;
    } catch {
      /* keep the plain message */
    }
    onError?.(why);
  };
  el.play().catch((e) => {
    // Refused without a tap, as a read-aloud reply can be on a phone. Not an error.
    if (e.name !== "NotAllowedError" && e.name !== "AbortError") onError?.(e.message);
    if (e.name === "NotAllowedError") done();
  });
}

export const speechPath = (sessionId, messageId) => `/sessions/${sessionId}/messages/${messageId}/speech`;
