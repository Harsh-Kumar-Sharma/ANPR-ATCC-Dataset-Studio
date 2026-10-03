import { useEffect } from "react";
import { isEditableTarget } from "../keyboard";
import type { FrameQueue } from "../useFrameQueue";
import UnsavedPrompt from "./UnsavedPrompt";

interface Props {
  queue: FrameQueue;
  /** Back to the contact sheet, where this frame was picked from. */
  onShowGrid?: () => void;
  /** One tight row for the canvas toolbar; the shortcut is listed
   *  with the canvas's own. */
  compact?: boolean;
}

/**
 * Previous and Next, under the image being labelled.
 *
 * They were only in the sidebar, so finishing a frame meant moving the
 * eye and the mouse away from the work after every single one. These
 * walk the same list the sidebar does - the queue is held above both -
 * so the two cannot disagree about which frame is next.
 *
 * Ctrl/Cmd with the arrow keys does the same thing. Bare arrows are
 * taken: they nudge the selected box by a pixel.
 */
function FrameNav({ queue, onShowGrid, compact = false }: Props) {
  const { index, frames, pending } = queue;

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (isEditableTarget(e.target)) return;
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      queue.step(e.key === "ArrowRight" ? 1 : -1, "canvas");
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // Re-registered every render so the handler always sees the current
    // list - the same shape the canvas uses.
  });

  return (
    <div className={`frame-nav${compact ? " frame-nav--compact" : ""}`}>
      <div className="frame-nav__row">
        {/* The way back. Picking the hundredth picture, labelling it
            and then having no route to the sheet is a dead end. */}
        {onShowGrid && (
          <button className="frame-nav__grid" onClick={onShowGrid}>
            &#9638; {compact ? "All" : "All frames"}
          </button>
        )}
        <button
          aria-label="Previous frame in the queue"
          disabled={!queue.canStep(-1)}
          onClick={() => queue.step(-1, "canvas")}
        >
          &larr; {compact ? "Prev" : "Previous"}
        </button>
        <span className="frame-nav__position" data-testid="frame-position">
          {index === -1 ? `${frames.length} frames` : `${index + 1} of ${frames.length}`}
        </span>
        <button
          aria-label="Next frame in the queue"
          disabled={!queue.canStep(1)}
          onClick={() => queue.step(1, "canvas")}
        >
          Next &rarr;
        </button>
      </div>

      {!compact && (
        <p className="frame-nav__hint">
          <span className="kbd">Ctrl</span>
          <span className="kbd">&larr;</span>/<span className="kbd">&rarr;</span> move between frames
        </p>
      )}

      {/* Only when this is what raised the question. */}
      {pending?.from === "canvas" && (
        <UnsavedPrompt className="frame-nav__prompt" frame={pending.frame} onResolve={queue.resolvePending} />
      )}
    </div>
  );
}

export default FrameNav;
