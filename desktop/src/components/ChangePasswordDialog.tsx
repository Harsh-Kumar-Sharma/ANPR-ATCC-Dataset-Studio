import { useState } from "react";
import { api, ApiError } from "../api";
import { IconAlert, IconCheck } from "../Icons";
import Modal from "./Modal";

interface Props {
  onClose: () => void;
}

/** Change your own password. Other windows you are signed in to are
 *  signed out; this one stays. */
function ChangePasswordDialog({ onClose }: Props) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (next !== confirm) {
      setError("The two new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await api.changeMyPassword(current, next);
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Change password" onClose={onClose}>
      {done ? (
        <>
          <p className="status">
            <IconCheck /> Password changed. Any other window you were signed in to has been signed out.
          </p>
          <div className="modal__actions">
            <button className="btn-primary" onClick={onClose}>
              Done
            </button>
          </div>
        </>
      ) : (
        <form className="auth-form" onSubmit={submit}>
          <label className="auth-field">
            Current password
            <input
              type="password"
              autoComplete="current-password"
              autoFocus
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </label>
          <label className="auth-field">
            New password
            <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
          </label>
          <label className="auth-field">
            Confirm new password
            <input
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </label>
          <p className="auth-hint">At least 8 characters.</p>
          {error && (
            <p className="error" role="alert">
              <IconAlert /> {error}
            </p>
          )}
          <div className="modal__actions">
            <button type="button" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={busy || !current || !next || !confirm}>
              {busy ? "Saving…" : "Change password"}
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}

export default ChangePasswordDialog;
