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
   *  send the reviewer. May reject - this panel is the only surface that
   *  can report it. */
  onSelectFrame: (frameId: string) => void | Promise<void>;
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
  /** Whether this project has any labels at all. `null` while unknown. */
  const [hasLabels, setHasLabels] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load(nextTab: Tab) {
    setTab(nextTab);
    setError(null);
    try {
      if (nextTab === "low-confidence") setQueue(await api.getLowConfidenceQueue(project.id));
      else if (nextTab === "hard-failed") setQueue(await api.getHardFailedQueue(project.id));
      else {
        // The balance comes along so an empty queue can tell "nothing
        // is wrong" apart from "nothing has been labelled".
        const [items, balance] = await Promise.all([
          api.getDisagreements(project.id),
          api.getLabelBalance(project.id).catch(() => null),
        ]);
        setDisagreements(items);
        setHasLabels(balance === null ? null : balance.total_boxes + balance.unclassified_boxes > 0);
      }
    } catch (e) {
      setError(String(e));
    }
  }

  async function openFrame(frameId: string) {
    try {
      await onSelectFrame(frameId);
    } catch (e) {
      setError(`Could not open that frame: ${String(e)}`);
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
                {item.kind === "unmatched_box"
                  ? `nothing the detector found matches this "${item.human_class_name}"`
                  : `detector saw "${item.detector_class}", human picked "${item.human_class_name}"`}
              </span>
              {/* A label made by reviewing a track opens that track,
                  which is where it was written. Everything else opens
                  its frame - a canvas box is not visible in a track. */}
              {item.track_id ? (
                <button onClick={() => reviewTrack(item.track_id!)}>Review track</button>
              ) : (
                <button onClick={() => openFrame(item.frame_id)}>Open frame</button>
              )}
            </li>
          ))}
          {/* "None." read the same whether the queue had looked and
              found nothing or had nothing to look at. Saying "every
              label agrees with the model" about a project with no
              labels was the same conflation one sentence further on, so
              the two cases are told apart rather than reworded. */}
          {disagreements.length === 0 &&
            (hasLabels === false ? (
              <li className="empty">Nothing labelled yet, so there is nothing to compare.</li>
            ) : (
              <li className="empty">Nothing to flag - every label agrees with the model.</li>
            ))}
        </ul>
      )}
    </div>
  );
}

export default ActiveLearningPanel;
