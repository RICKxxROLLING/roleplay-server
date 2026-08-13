import { useState } from "react";
import { api } from "../api";

/**
 * Shown instead of the app when a password is set and no session exists.
 *
 * Deliberately says nothing about the server -- no character names, no chat
 * count, no backend status. Everything on this screen is visible to anyone who
 * can reach the port, so it carries the minimum needed to sign in.
 */
export default function LoginScreen({ onSignedIn }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function submit(e) {
    e?.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.login(password);
      onSignedIn?.();
    } catch (err) {
      setError(err.message || "Incorrect password.");
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="h-full grid place-items-center bg-ink-950">
      <form
        onSubmit={submit}
        className="w-[320px] bg-ink-900 border border-ink-700 rounded-xl p-6 space-y-4"
      >
        <div>
          <h1 className="font-semibold">Roleplay</h1>
          <p className="text-xs text-slate-500 mt-1">Sign in to continue.</p>
        </div>

        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          autoFocus
          autoComplete="current-password"
          className="w-full bg-ink-950 border border-ink-800 rounded-lg px-3 py-2 text-sm outline-none focus:border-accent/60 placeholder:text-slate-600"
        />

        {error && <p className="text-xs text-rose-400">{error}</p>}

        <button
          type="submit"
          disabled={busy || !password}
          className="w-full text-sm py-2 rounded-lg bg-accent/25 border border-accent/40 hover:bg-accent/35 disabled:opacity-40"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
