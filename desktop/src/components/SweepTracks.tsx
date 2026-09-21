import { useState } from "react";
import { api, ApiError } from "../api";
import type { Project, TrackSweep } from "../types";

interface Props {
  project: Project;
  /** Told once they have gone, so the list catches up. */
  onSwept: () => void;
}

/** Bytes as something a person can weigh a decision against. */
function readableSize(bytes: number): string {
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/**
 * Clear out the detections nobody accepted.
 *
 * Reviewing is: look at what the model found, accept the ones worth
 * keeping, and then be left with a list of a hundred you do not
 * want. Deleting those one at a time is tidying, not reviewing.
 */
function SweepTracks({ project, onSwept }: Props) {
  const [asked, setAsked] = useState<TrackSweep | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<TrackSweep | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function ask() {
    setError(null);
    setDone(null);
    try {
      setAsked(await api.previewTrackSweep(project.id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      setDone(await api.sweepUnacceptedTracks(project.id));
      setAsked(null);
      onSwept();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="sweep">
      {asked === null ? (
        <button className="sweep__start" onClick={ask}>
          Delete the detections I did not accept
        </button>
      ) : (
        <div className="sweep__confirm" role="alert">
          {asked.deleted === 0 ? (
            <p>Nothing to delete - every detection here has been reviewed and kept.</p>
          ) : (
            <p>
              This deletes <strong>{asked.deleted}</strong> detection(s) and the{" "}
              <strong>{asked.frames_deleted}</strong> frame(s) left holding nothing, freeing{" "}
              {readableSize(asked.bytes_freed)}. <strong>{asked.kept}</strong> accepted or flagged stay.
              It cannot be undone.
            </p>
          )}
          <div className="sweep__row">
            {asked.deleted > 0 && (
              <button className="danger" disabled={busy} onClick={confirm}>
                {busy ? "Deleting…" : "Delete them"}
              </button>
            )}
            <button disabled={busy} onClick={() => setAsked(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {done && (
        <p className="status">
          Deleted {done.deleted} detection(s) and {done.frames_deleted} frame(s), freeing{" "}
          {readableSize(done.bytes_freed)}.
          {/* A detection that survived a delete the user asked for
              needs a reason. */}
          {done.held > 0
            ? ` ${done.held} stayed because a dataset version you already exported names them.`
            : ""}
        </p>
      )}

      {error && <p className="error">{error}</p>}
    </div>
  );
}

export default SweepTracks;
