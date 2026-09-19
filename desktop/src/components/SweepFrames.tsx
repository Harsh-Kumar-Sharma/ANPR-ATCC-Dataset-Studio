import { useState } from "react";
import { api, ApiError } from "../api";
import type { Project, Sweep } from "../types";

interface Props {
  project: Project;
  /** Narrow the sweep to the source the queue is showing. */
  sourceId: string | null;
  /** Bumped so the queue reloads once frames have gone. */
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
 * Keep the frames you labelled; delete the rest.
 *
 * The other half of saving a live session's frames. Keeping them is
 * what makes a session useful when detection finds nothing; this is
 * what keeps it affordable, because most of them are a road with
 * nothing on it at a quarter of a megabyte each.
 */
function SweepFrames({ project, sourceId, onSwept }: Props) {
  const [asked, setAsked] = useState<Sweep | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Sweep | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function ask() {
    setError(null);
    setDone(null);
    try {
      setAsked(await api.previewSweep(project.id, sourceId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.sweepUnlabelled(project.id, sourceId);
      setDone(result);
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
          Delete the frames I did not label
        </button>
      ) : (
        <div className="sweep__confirm" role="alert">
          {asked.deleted === 0 ? (
            <p>Nothing to delete{sourceId ? " in this source" : ""} - every frame here has been labelled.</p>
          ) : (
            <p>
              This deletes <strong>{asked.deleted}</strong> unlabelled frame(s) and frees{" "}
              {readableSize(asked.bytes_freed)}. <strong>{asked.kept}</strong> labelled frame(s) stay. It
              cannot be undone.
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
          Deleted {done.deleted} frame(s), freed {readableSize(done.bytes_freed)}.
          {/* Said plainly rather than buried: a frame that stayed when
              the user asked for it to go needs a reason. */}
          {done.held > 0
            ? ` ${done.held} stayed because a dataset version you already exported names them.`
            : ""}
        </p>
      )}

      {error && <p className="error">{error}</p>}
    </div>
  );
}

export default SweepFrames;
