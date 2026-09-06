import { useState } from "react";
import { api } from "../api";
import type { DisagreementItem, Project, QueueItem, Track } from "../types";

interface Props {
  project: Project;
  tracks: Track[];
  onSelectTrack: (track: Track) => void;
}

type Tab = "low-confidence" | "hard-failed" | "disagreements";

function ActiveLearningPanel({ project, tracks, onSelectTrack }: Props) {
  const [tab, setTab] = useState<Tab | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [disagreements, setDisagreements] = useState<DisagreementItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function load(nextTab: Tab) {
    setTab(nextTab);
    setError(null);
    try {
      if (nextTab === "low-confidence") setQueue(await api.getLowConfidenceQueue(project.id));
      else if (nextTab === "hard-failed") setQueue(await api.getHardFailedQueue(project.id));
      else setDisagreements(await api.getDisagreements(project.id));
    } catch (e) {
      setError(String(e));
    }
  }

  function reviewTrack(trackId: string) {
    const track = tracks.find((t) => t.id === trackId);
    if (track) onSelectTrack(track);
  }

  return (
    <div className="active-learning-panel">
      <h3>Active Learning</h3>
      <div className="al-tabs">
        <button className={tab === "low-confidence" ? "selected" : ""} onClick={() => load("low-confidence")}>
          Low confidence
        </button>
        <button className={tab === "hard-failed" ? "selected" : ""} onClick={() => load("hard-failed")}>
          Hard/Failed
        </button>
        <button className={tab === "disagreements" ? "selected" : ""} onClick={() => load("disagreements")}>
          Disagreements
        </button>
      </div>
      {error && <p className="error">{error}</p>}

      {(tab === "low-confidence" || tab === "hard-failed") && (
        <ul className="al-queue-list">
          {queue.map((item) => (
            <li key={item.track_id}>
              <span>
                {item.bucket ?? "?"} / {item.review_status} - {(item.confidence * 100).toFixed(0)}%
              </span>
              <button onClick={() => reviewTrack(item.track_id)}>Review</button>
            </li>
          ))}
          {queue.length === 0 && <li className="empty">Empty.</li>}
        </ul>
      )}

      {tab === "disagreements" && (
        <ul className="al-queue-list">
          {disagreements.map((item) => (
            <li key={item.annotation_id}>
              <span>
                detector saw "{item.detector_class}", human picked "{item.human_class_name}"
              </span>
              <button onClick={() => reviewTrack(item.track_id)}>Review</button>
            </li>
          ))}
          {disagreements.length === 0 && <li className="empty">None.</li>}
        </ul>
      )}
    </div>
  );
}

export default ActiveLearningPanel;
