import { useEffect, useState } from "react";
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
  const [code, setCode] = useState("");
  // Whether two-factor is on. Public in /auth/status: the screen must know to
  // ask, and knowing it is on tells a guesser nothing useful.
  const [totp, setTotp] = useState(false);

  useEffect(() => {
    api.authStatus().then((s) => setTotp(!!s.totp)).catch(() => {});
  }, []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function submit(e) {
    e?.preventDefault();
    if (!password || busy || (totp && !code.trim())) return;
    setBusy(true);
    setError(null);
    try {
      await api.login(password, code.trim());
      onSignedIn?.();
    } catch (err) {
      setError(err.message || "Incorrect password.");
      setPassword("");
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="h-full grid place-items-center bg-ink-950 p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-[320px] bg-ink-900 border border-ink-700 rounded-xl p-6 space-y-4"
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

        {totp && (
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="Code from your authenticator app"
            inputMode="numeric"
            autoComplete="one-time-code"
            className="w-full bg-ink-950 border border-ink-800 rounded-lg px-3 py-2 text-sm outline-none focus:border-accent/60 placeholder:text-slate-600 tracking-widest"
          />
        )}
        {totp && (
          <p className="text-[11px] text-slate-600 -mt-2">Lost your phone? A recovery code works here too.</p>
        )}

        {error && <p className="text-xs text-rose-400">{error}</p>}

        <button
          type="submit"
          disabled={busy || !password || (totp && !code.trim())}
          className="w-full text-sm py-2 rounded-lg bg-accent/25 border border-accent/40 hover:bg-accent/35 disabled:opacity-40"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
