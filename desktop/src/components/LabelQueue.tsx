import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import type { Frame, Project, QueueProgress } from "../types";

interface Props {
  project: Project;
  selectedFrameId: string | null;
  /** True while the open frame has boxes that have not been saved. */
  dirty?: boolean;
  /** Bumped when a frame is saved or skipped, so the list catches up. */
  refreshKey?: number;
  onSelect: (frame: Frame) => void;
}

/** Where the user last was, per project. Browser storage rather than the
 *  database: it is this machine's view of a shared project, and losing
 *  it costs a scroll, not work. */
const lastFrameKey = (projectId: string) => `anpr:last-frame:${projectId}`;

function readLastFrame(projectId: string): string | null {
  try {
    return window.localStorage.getItem(lastFrameKey(projectId));
  } catch {
    return null;
  }
}

function writeLastFrame(projectId: string, frameId: string): void {
  try {
    window.localStorage.setItem(lastFrameKey(projectId), frameId);
  } catch {
    // Private mode, or storage full. Not worth interrupting labelling.
  }
}

/**
 * The frames waiting to be labelled: what is left, what is done, and
 * how to walk through them.
 *
 * Navigation is guarded rather than blocked. Walking away from unsaved
 * boxes asks first, because the alternative is either losing work
 * silently or refusing to move at all, and both are worse than a
 * question.
 */
function LabelQueue({ project, selectedFrameId, dirty = false, refreshKey = 0, onSelect }: Props) {
  const [frames, setFrames] = useState<Frame[]>([]);
  const [progress, setProgress] = useState<QueueProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The frame the user asked for while unsaved work was open.
  const [pending, setPending] = useState<Frame | null>(null);
  const [resumed, setResumed] = useState(false);
  // Frames out of the queue: rejected by a human, or passed over by
  // selection. Shown together because the question the user is asking
  // is the same one - what am I not being offered?
  const [showSetAside, setShowSetAside] = useState<boolean>(false);
  // Only the newest request may write. Two refreshes racing (rapid saves
  // each bumping refreshKey) could otherwise land out of order and leave
  // the list describing an older state than the counts.
  const latestRequest = useRef(0);

  const describe = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

  const refresh = useCallback(async () => {
    const request = ++latestRequest.current;
    try {
      const [list, counts] = await Promise.all([
        showSetAside
          ? Promise.all([api.listFrames(project.id, "rejected"), api.listFrames(project.id, "skipped")]).then(
              ([rejected, skipped]) => [...rejected, ...skipped].sort((a, b) => a.frame_index - b.frame_index),
            )
          : api.listFrames(project.id),
        api.getQueueProgress(project.id),
      ]);
      if (request !== latestRequest.current) return null;
      setFrames(list);
      setProgress(counts);
      setError(null);
      return list;
    } catch (e) {
      if (request === latestRequest.current) setError(describe(e));
      return null;
    }
  }, [project.id, showSetAside]);

  useEffect(() => {
    let cancelled = false;
    refresh().then((list) => {
      if (cancelled || !list) return;
      // Put the user back where they were, once, and only if they are
      // not already looking at something.
      if (resumed || selectedFrameId !== null) return;
      setResumed(true);
      const last = readLastFrame(project.id);
      const frame = list.find((f) => f.id === last);
      if (frame) onSelect(frame);
    });
    return () => {
      cancelled = true;
    };
    // onSelect and selectedFrameId are deliberately not dependencies:
    // resuming is a one-shot on arrival, not a reaction to selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, refreshKey]);

  useEffect(() => {
    if (selectedFrameId) writeLastFrame(project.id, selectedFrameId);
  }, [project.id, selectedFrameId]);

  function open(frame: Frame) {
    if (frame.id === selectedFrameId) return;
    if (dirty) {
      setPending(frame);
      return;
    }
    onSelect(frame);
  }

  const index = frames.findIndex((f) => f.id === selectedFrameId);
  const step = (by: 1 | -1) => {
    const next = frames[index === -1 ? (by === 1 ? 0 : frames.length - 1) : index + by];
    if (next) open(next);
  };

  return (
    <section className="label-queue">
      <h3>Frames</h3>

      {progress && progress.total > 0 && (
        <p className="label-queue__progress" data-testid="queue-progress">
          {progress.labeled} labelled &middot; {progress.rejected + progress.skipped} set aside &middot;{" "}
          {progress.pending} left
        </p>
      )}

      {progress !== null && (progress.rejected > 0 || progress.skipped > 0) && (
        <label className="label-queue__toggle">
          <input type="checkbox" checked={showSetAside} onChange={(e) => setShowSetAside(e.target.checked)} />
          Show set-aside frames
        </label>
      )}

      {error && <p className="label-queue__error">{error}</p>}

      {pending && (
        <div className="label-queue__prompt" role="alert">
          <p>This frame has unsaved boxes.</p>
          <div className="label-queue__prompt-row">
            <button
              onClick={() => {
                const frame = pending;
                setPending(null);
                onSelect(frame);
              }}
            >
              Discard and open frame {pending.frame_index}
            </button>
            <button onClick={() => setPending(null)}>Stay here</button>
          </div>
        </div>
      )}

      {frames.length === 0 ? (
        <p className="label-queue__empty">
          {showSetAside ? "Nothing has been set aside." : "No frames yet - run detection on a source first."}
        </p>
      ) : (
        <>
          <div className="label-queue__nav">
            <button aria-label="Previous frame" disabled={index === 0} onClick={() => step(-1)}>
              &larr; Prev
            </button>
            <button aria-label="Next frame" disabled={index === frames.length - 1} onClick={() => step(1)}>
              Next &rarr;
            </button>
          </div>
          <ul className="label-queue__list">
            {frames.map((frame) => (
              <li key={frame.id}>
                <button
                  className={frame.id === selectedFrameId ? "selected" : ""}
                  onClick={() => open(frame)}
                  aria-label={`Frame ${frame.frame_index}`}
                >
                  <span>frame {frame.frame_index}</span>
                  <span className={`label-queue__status label-queue__status--${frame.status}`}>{frame.status}</span>
                </button>
                {frame.selection_reason && (
                  <p className="label-queue__reason" title={frame.selection_reason}>
                    {frame.selection_reason}
                  </p>
                )}
                {(frame.status === "rejected" || frame.status === "skipped") && (
                  <button
                    className="label-queue__restore"
                    aria-label={`Put frame ${frame.frame_index} back`}
                    onClick={async () => {
                      await api.setFrameStatus(frame.id, "pending");
                      refresh();
                    }}
                  >
                    Put back
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

export default LabelQueue;
