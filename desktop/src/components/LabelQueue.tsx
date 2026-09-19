import { api } from "../api";
import { sourceLabel } from "../sourceLabel";
import SweepFrames from "./SweepFrames";
import type { Project } from "../types";
import type { FrameQueue } from "../useFrameQueue";
import UnsavedPrompt from "./UnsavedPrompt";

interface Props {
  queue: FrameQueue;
  selectedFrameId: string | null;
  /** Needed to clear out the frames nobody labelled. */
  project: Project;
}

/**
 * The frames waiting to be labelled: what is left, what is done, and
 * which one is open.
 *
 * The list itself lives in useFrameQueue, because the Previous/Next
 * under the canvas walks the same one. This panel shows it.
 */
function LabelQueue({ queue, selectedFrameId, project }: Props) {
  const { frames, progress, sources, sourceId, showSetAside, error, pending } = queue;

  return (
    <section className="label-queue">
      <h3>Frames</h3>

      {/* One source is not a choice, so it is not offered. */}
      {sources.length > 1 && (
        <label className="queue-source">
          <span>Source</span>
          <select
            aria-label="Source to label"
            value={sourceId ?? ""}
            onChange={(e) => queue.chooseSource(e.target.value || null)}
          >
            <option value="">All sources &middot; {sources.reduce((n, s) => n + s.pending, 0)} left</option>
            {sources.map((s) => (
              <option key={s.source_id} value={s.source_id}>
                {sourceLabel(s)} &middot; {s.pending} left of {s.total}
              </option>
            ))}
          </select>
        </label>
      )}

      {progress && progress.total > 0 && (
        <p className="label-queue__progress" data-testid="queue-progress">
          {progress.labeled} labelled &middot; {progress.rejected + progress.skipped} set aside &middot;{" "}
          {progress.pending} left
        </p>
      )}

      {progress !== null && (progress.rejected > 0 || progress.skipped > 0) && (
        <label className="label-queue__toggle">
          <input
            type="checkbox"
            checked={showSetAside}
            onChange={(e) => queue.setShowSetAside(e.target.checked)}
          />
          Show set-aside frames
        </label>
      )}

      {error && <p className="label-queue__error">{error}</p>}

      {/* Only when the move was asked for here. A question raised by
          the buttons under the canvas belongs under the canvas. */}
      {pending?.from === "queue" && (
        <UnsavedPrompt className="label-queue__prompt" frame={pending.frame} onResolve={queue.resolvePending} />
      )}

      {frames.length === 0 ? (
        <p className="label-queue__empty">
          {showSetAside ? "Nothing has been set aside." : "No frames yet - run detection on a source first."}
        </p>
      ) : (
        <>
          <div className="label-queue__nav">
            <button aria-label="Previous frame" disabled={!queue.canStep(-1)} onClick={() => queue.step(-1)}>
              &larr; Prev
            </button>
            <button aria-label="Next frame" disabled={!queue.canStep(1)} onClick={() => queue.step(1)}>
              Next &rarr;
            </button>
          </div>
          <ul className="label-queue__list">
            {frames.map((frame) => (
              <li key={frame.id}>
                <button
                  className={frame.id === selectedFrameId ? "selected" : ""}
                  onClick={() => queue.open(frame)}
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
                      queue.refresh();
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

      {/* After the list, because it acts on what is in it - and only
          once there is something to act on. */}
      {frames.length > 0 && (
        <SweepFrames project={project} sourceId={sourceId} onSwept={() => queue.refresh()} />
      )}
    </section>
  );
}

export default LabelQueue;
