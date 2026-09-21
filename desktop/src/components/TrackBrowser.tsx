import SweepTracks from "./SweepTracks";
import { IconBox } from "../Icons";
import type { Project, Track } from "../types";

interface Props {
  tracks: Track[];
  selectedTrackId: string | null;
  onSelect: (track: Track) => void;
  /** Needed to clear out the detections nobody accepted. */
  project?: Project;
  /** Told once they have gone, so the list catches up. */
  onSwept?: () => void;
}

const BUCKET_LABEL: Record<string, string> = {
  BEST_DETECTION: "Best",
  HARD: "Hard",
  FAILED: "Failed",
};

function TrackBrowser({ tracks, selectedTrackId, onSelect, project, onSwept }: Props) {
  return (
    <div className="track-browser">
      <div className="section-title">
        <IconBox /> Tracks ({tracks.length})
      </div>

      {/* Above the list, not after it: below a hundred rows it may as
          well not exist. */}
      {project && tracks.length > 0 && <SweepTracks project={project} onSwept={onSwept ?? (() => {})} />}
      <ul className="track-list">
        {tracks.map((t) => (
          <li key={t.id} className={t.id === selectedTrackId ? "selected" : ""}>
            <button onClick={() => onSelect(t)}>
              <span className={`badge bucket-${t.bucket ?? "unknown"}`}>
                {t.bucket ? BUCKET_LABEL[t.bucket] : "?"}
              </span>
              <span className={`badge review-${t.review_status}`}>{t.review_status}</span>
              <span className="ts">
                {t.start_ts}–{t.end_ts}ms
              </span>
            </button>
          </li>
        ))}
        {tracks.length === 0 && <li className="empty">No tracks yet - process a source first.</li>}
      </ul>
    </div>
  );
}

export default TrackBrowser;
