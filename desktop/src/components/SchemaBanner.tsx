import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { IconAlert, IconCheck } from "../Icons";
import type { SchemaState } from "../types";

interface Props {
  /** Told once the database is up to date, so whatever failed because
   *  of it can be tried again. */
  onUpgraded?: () => void;
}

/**
 * The database is behind the app, and that is why something failed.
 *
 * Every time this app gains a table, the database on disk is one
 * migration behind until someone runs alembic. What that looked like
 * from inside the app was a 500 and "An unexpected error occurred" -
 * in the training panel, or the live tab, or wherever the new table
 * happened to be used. The user goes looking for a bug that is not
 * there.
 */
function SchemaBanner({ onUpgraded }: Props) {
  const [state, setState] = useState<SchemaState | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(async () => {
    try {
      setState(await api.getSchemaState());
    } catch {
      // An app that cannot say whether its database is current says
      // nothing, rather than crying wolf on a blip.
      setState(null);
    }
  }, []);

  useEffect(() => {
    check();
  }, [check]);

  async function upgrade() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.upgradeSchema();
      setState(result.state);
      setDone(result.backup_path);
      onUpgraded?.();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (state === null || state.up_to_date) {
    // Shown only just after an upgrade, so the backup's location does
    // not vanish before it has been read.
    if (done) {
      return (
        <div className="schema-banner schema-banner--ok" role="status">
          <IconCheck /> Database updated. The old one was saved to {done}.
        </div>
      );
    }
    return null;
  }

  return (
    <div className="schema-banner" role="alert">
      <span className="schema-banner__text">
        <IconAlert /> This app expects a newer database than the one on disk
        {state.pending.length > 0 ? ` (${state.pending.length} update(s) pending)` : ""}. Things that use
        anything new will fail until it is brought up to date.
      </span>
      <button disabled={busy} onClick={upgrade}>
        {busy ? "Updating…" : "Update the database"}
      </button>
      <span className="schema-banner__note">A copy is saved first, so this can be undone.</span>
      {error && <span className="schema-banner__note error">{error}</span>}
    </div>
  );
}

export default SchemaBanner;
