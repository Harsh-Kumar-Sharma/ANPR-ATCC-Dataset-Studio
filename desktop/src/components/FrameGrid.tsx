import { useEffect, useRef } from "react";
import { api } from "../api";
import type { Frame } from "../types";
import { IconX } from "../Icons";
import { POSSIBLE_MISS } from "../useFrameQueue";

interface Props {
  frames: Frame[];
  selectedFrameId: string | null;
  /** Open one for labelling. */
  onOpen: (frame: Frame) => void;
  /** Offered as an X when the sheet is a view of its own. */
  onClose?: () => void;
}

/** Where the sheet was scrolled to when it was last closed.
 *
 *  Module-level rather than component state because the grid
 *  unmounts the moment a frame is opened - which is exactly when the
 *  position needs to survive. Per session, deliberately: it is a
 *  scroll offset, not a preference worth storing. */
let lastOffset = 0;

/**
 * Every frame at once, as pictures.
 *
 * The queue is a list of "frame 0, frame 7, frame 14", which says
 * nothing about which of two hundred frames has a vehicle in it.
 * Looking for the four worth labelling meant opening them one at a
 * time. A contact sheet is how that is normally done, and clicking
 * one goes straight to labelling it.
 *
 * Thumbnails, not frames: two hundred full-size images would be tens
 * of megabytes and, for a video source, two hundred decodes written
 * to disk.
 */
function FrameGrid({ frames, selectedFrameId, onOpen, onClose }: Props) {
  const list = useRef<HTMLUListElement>(null);
  const selected = useRef<HTMLButtonElement>(null);

  // Come back where you left off. Labelling from the sheet is: pick
  // the hundredth picture, label it, come back - and coming back to
  // the top of two hundred thumbnails means finding your place again
  // every single time.
  useEffect(() => {
    if (selected.current) {
      // The frame just labelled, in the middle of the view. Better
      // than a saved offset: it is right even when the list has
      // changed underneath, which it does as frames get labelled.
      selected.current.scrollIntoView({ block: "center" });
      return;
    }
    if (list.current) list.current.scrollTop = lastOffset;
    // Once, on open. Scrolling the user somewhere while they browse
    // would be worse than not restoring at all.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const element = list.current;
    return () => {
      if (element) lastOffset = element.scrollTop;
    };
  }, []);

  return (
    <div className="frame-grid">
      <div className="player-header">
        <h2>All frames ({frames.length})</h2>
        {onClose && (
          <button className="btn-ghost" onClick={onClose} title="Back to the frame being labelled">
            <IconX />
          </button>
        )}
      </div>

      {frames.length === 0 ? (
        <p className="live-waiting">Nothing in the queue to show.</p>
      ) : (
        <ul className="frame-grid__items" ref={list}>
          {frames.map((frame) => (
            <li key={frame.id}>
              <button
                ref={frame.id === selectedFrameId ? selected : undefined}
                className={`frame-grid__item${frame.id === selectedFrameId ? " selected" : ""}`}
                onClick={() => onOpen(frame)}
                aria-label={`Label frame ${frame.frame_index}`}
              >
                {/* Lazily, so opening the grid does not ask for two
                    hundred images the user may never scroll to. */}
                <img src={api.frameThumbnailUrl(frame.id)} alt="" loading="lazy" decoding="async" />
                {frame.selection_reason === POSSIBLE_MISS && (
                  <span className="frame-grid__miss" title="A vehicle with no plate found by the model">
                    possible miss
                  </span>
                )}
                <span className="frame-grid__caption">
                  <span>frame {frame.frame_index}</span>
                  <span className={`label-queue__status label-queue__status--${frame.status}`}>
                    {frame.status}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default FrameGrid;
