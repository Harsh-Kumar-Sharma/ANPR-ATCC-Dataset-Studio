import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Frame, Project } from "../types";

interface Props {
  project: Project;
  selectedFrameId: string | null;
  /** Bumped when a frame is saved, so statuses here catch up. */
  refreshKey?: number;
  onSelect: (frame: Frame) => void;
}

/**
 * The frames waiting to be labelled.
 *
 * Deliberately a flat list for now: next/previous, progress, and
 * rejecting a frame are the next ticket. This exists so a frame can be
 * opened at all.
 */
function LabelQueue({ project, selectedFrameId, refreshKey = 0, onSelect }: Props) {
  const [frames, setFrames] = useState<Frame[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listFrames(project.id)
      .then((list) => {
        setFrames(list);
        setError(null);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [project.id, refreshKey]);

  const labeled = frames.filter((f) => f.status === "labeled").length;

  return (
    <section className="label-queue">
      <h3>
        Frames{frames.length > 0 && ` · ${labeled} / ${frames.length} labelled`}
      </h3>
      {error && <p className="label-queue__error">{error}</p>}
      {frames.length === 0 ? (
        <p className="label-queue__empty">No frames yet - run detection on a source first.</p>
      ) : (
        <ul className="label-queue__list">
          {frames.map((frame) => (
            <li key={frame.id}>
              <button className={frame.id === selectedFrameId ? "selected" : ""} onClick={() => onSelect(frame)}>
                <span>frame {frame.frame_index}</span>
                <span className={`label-queue__status label-queue__status--${frame.status}`}>{frame.status}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default LabelQueue;
