import { api } from "../api";
import type { Frame } from "../types";
import { IconX } from "../Icons";

interface Props {
  frames: Frame[];
  selectedFrameId: string | null;
  /** Open one for labelling. */
  onOpen: (frame: Frame) => void;
  onClose: () => void;
}

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
  return (
    <div className="frame-grid">
      <div className="player-header">
        <h2>All frames ({frames.length})</h2>
        <button className="btn-ghost" onClick={onClose} title="Back to the frame being labelled">
          <IconX />
        </button>
      </div>

      {frames.length === 0 ? (
        <p className="live-waiting">Nothing in the queue to show.</p>
      ) : (
        <ul className="frame-grid__items">
          {frames.map((frame) => (
            <li key={frame.id}>
              <button
                className={`frame-grid__item${frame.id === selectedFrameId ? " selected" : ""}`}
                onClick={() => onOpen(frame)}
                aria-label={`Label frame ${frame.frame_index}`}
              >
                {/* Lazily, so opening the grid does not ask for two
                    hundred images the user may never scroll to. */}
                <img src={api.frameThumbnailUrl(frame.id)} alt="" loading="lazy" decoding="async" />
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
