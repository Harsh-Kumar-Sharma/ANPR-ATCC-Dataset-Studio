import { useState } from "react";
import { api } from "../api";
import { IconAlert, IconInbox } from "../Icons";
import type { DisagreementItem, Project, QueueItem, Track } from "../types";

interface Props {
  project: Project;
  tracks: Track[];
  onSelectTrack: (track: Track) => void;
  /** Open a frame in the labelling canvas. A box drawn there belongs to
   *  a frame and to no track, so a track-review button has nowhere to
   *  send the reviewer. */
  onSelectFrame: (frameId: string) => void;
}

type Tab = "low-confidence" | "hard-failed" | "disagreements";

const TAB_LABEL: Record<Tab, string> = {
  "low-confidence": "Low confidence",
  "hard-failed": "Hard/Failed",
  disagreements: "Disagreements",
};

function ActiveLearningPanel({ project, tracks, onSelectTrack, onSelectFrame }: Props) {
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
      <div className="section-title">
        <IconInbox /> Active Learning
      </div>

      <div className="al-tabs">
        {(Object.keys(TAB_LABEL) as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "selected" : ""} onClick={() => load(t)}>
            {TAB_LABEL[t]}
          </button>
        ))}
      </div>

      {error && (
        <p className="error">
          <IconAlert /> {error}
        </p>
      )}

      {(tab === "low-confidence" || tab === "hard-failed") && (
        <ul className="al-queue-list">
          {queue.map((item) => (
            <li key={item.track_id}>
              <span>
                <span className={`badge bucket-${item.bucket ?? "unknown"}`} style={{ marginRight: "0.4rem" }}>
                  {item.bucket ?? "?"}
                </span>
                {(item.confidence * 100).toFixed(0)}% confidence
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
                {item.kind === "missed_detection"
                  ? `detector found nothing here, human drew a "${item.human_class_name}"`
                  : `detector saw "${item.detector_class}", human picked "${item.human_class_name}"`}
              </span>
              {/* A label made by reviewing a track opens that track,
                  which is where it was written. Everything else opens
                  its frame - a canvas box is not visible in a track. */}
              {item.track_id ? (
                <button onClick={() => reviewTrack(item.track_id!)}>Review track</button>
              ) : (
                <button onClick={() => onSelectFrame(item.frame_id)}>Open frame</button>
              )}
            </li>
          ))}
          {/* "None." read the same whether the queue had looked and
              found nothing or could not see canvas labels at all. It can
              see them now, so it can say so. */}
          {disagreements.length === 0 && <li className="empty">Nothing to flag - every label agrees with the model.</li>}
        </ul>
      )}
    </div>
  );
}

export default ActiveLearningPanel;
