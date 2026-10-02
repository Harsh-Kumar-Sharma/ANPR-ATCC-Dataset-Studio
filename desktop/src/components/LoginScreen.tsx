import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { IconAlert, IconFolder, IconLock } from "../Icons";
import type { AuthStatus } from "../types";

interface Props {
  /** Bumped when the database is brought up to date, so a studio whose
   *  users table did not exist yet can be asked again. */
  schemaVersion?: number;
}

/**
 * The way in.
 *
 * Two forms in one place: signing in, and - the first time the studio
 * is opened, when nobody exists yet - creating the admin who will add
 * everyone else.
 */
function LoginScreen({ schemaVersion = 0 }: Props) {
  const { signedIn, expiredMessage } = useAuth();
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [unreachable, setUnreachable] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(async () => {
    try {
      setStatus(await api.getAuthStatus());
      setUnreachable(null);
    } catch (e) {
      setUnreachable(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    check();
  }, [check, schemaVersion]);

  const setup = status?.needs_setup === true;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (setup && password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const result = setup
        ? await api.setupAdmin(username.trim(), password, displayName.trim())
        : await api.login(username.trim(), password);
      signedIn(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setPassword("");
      setConfirm("");
    } finally {
      setBusy(false);
    }
  }

  const ready = status !== null && status.database_ready;

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="project-picker-brand">
          <span className="brand-icon">
            <IconFolder />
          </span>
          <h1>ANPR + ATCC Dataset Studio</h1>
        </div>
        <p className="project-picker-sub">
          {setup
            ? "First time here. Create the admin account - the admin adds everyone else."
            : "Sign in to continue."}
        </p>

        {unreachable && (
          <p className="error auth-gap">
            <IconAlert /> Could not reach the backend: {unreachable}
          </p>
        )}
        {status && !status.database_ready && (
          <p className="warning auth-gap">
            <IconAlert /> The database needs updating before anyone can sign in. Use “Update the database” above.
          </p>
        )}
        {expiredMessage && !error && <p className="warning auth-gap">{expiredMessage}</p>}

        {ready && (
          <form className="auth-form" onSubmit={submit}>
            <label className="auth-field">
              Username
              <input
                type="text"
                autoComplete="username"
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            {setup && (
              <label className="auth-field">
                Your name <span className="auth-optional">(optional)</span>
                <input type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
              </label>
            )}
            <label className="auth-field">
              Password
              <input
                type="password"
                autoComplete={setup ? "new-password" : "current-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {setup && (
              <label className="auth-field">
                Confirm password
                <input
                  type="password"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                />
              </label>
            )}
            {setup && <p className="auth-hint">At least 8 characters.</p>}

            {error && (
              <p className="error" role="alert">
                <IconAlert /> {error}
              </p>
            )}

            <button
              type="submit"
              className="btn-primary btn-block auth-submit"
              disabled={busy || !username.trim() || !password || (setup && !confirm)}
            >
              <IconLock />
              {busy ? (setup ? "Creating…" : "Signing in…") : setup ? "Create admin and sign in" : "Sign in"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

export default LoginScreen;
