import { useState } from "react";
import { api } from "../api";
import type { EvaluationReport, Track } from "../types";

interface Props {
  tracks: Track[];
}

function latestRunId(tracks: Track[]): string | null {
  if (tracks.length === 0) return null;
  return tracks.reduce((latest, t) => (t.created_at > latest.created_at ? t : latest)).run_id;
}

function EvaluationPanel({ tracks }: Props) {
  const [report, setReport] = useState<EvaluationReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleLoad() {
    const runId = latestRunId(tracks);
    if (!runId) {
      setError("No processing run yet - run Detect + Track first.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setReport(await api.getRunEvaluation(runId));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="evaluation-panel">
      <h3>Evaluation (latest run)</h3>
      <button disabled={loading} onClick={handleLoad}>
        {loading ? "Loading..." : "Load Evaluation"}
      </button>
      {error && <p className="error">{error}</p>}
      {report && (
        <div className="evaluation-report">
          <p>
            Tracks: {report.track_counts.confirmed} confirmed / {report.track_counts.failed} failed /{" "}
            {report.track_counts.unreviewed} unreviewed (of {report.track_counts.total})
          </p>
          <p>
            Detection recall:{" "}
            {report.detection_recall === null
              ? "n/a (freeze this source with a ground-truth count first)"
              : `${(report.detection_recall * 100).toFixed(0)}%`}
          </p>
          <p>
            Duplicate track pairs: {report.duplicate_track_pairs.length}, fragmented track pairs:{" "}
            {report.fragmented_track_pairs.length}
          </p>
          <p>
            OCR agreement:{" "}
            {report.ocr_metrics.agreement_rate === null
              ? "n/a"
              : `${(report.ocr_metrics.agreement_rate * 100).toFixed(0)}% (${report.ocr_metrics.tracks_with_ocr} track(s))`}
          </p>
          <p>Class distribution:</p>
          <ul className="class-distribution-list">
            {Object.entries(report.class_distribution).map(([name, count]) => (
              <li key={name}>
                {name}: {count}
              </li>
            ))}
            {Object.keys(report.class_distribution).length === 0 && <li className="empty">None yet.</li>}
          </ul>
          <p>Failure gallery ({report.failure_gallery.length}):</p>
          <div className="failure-gallery">
            {report.failure_gallery.map((item) => (
              <img
                key={item.track_id}
                src={item.representative_frame_id ? api.frameImageUrl(item.representative_frame_id) : undefined}
                alt={item.track_id}
                title={`${item.bucket ?? "?"} / ${item.review_status}`}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default EvaluationPanel;
