import { useState } from "react";
import { api } from "../api";
import { IconAlert, IconChart } from "../Icons";
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
      <div className="section-title">
        <IconChart /> Evaluation (latest run)
      </div>

      <button className="btn-primary btn-block" disabled={loading} onClick={handleLoad}>
        {loading ? "Loading…" : "Load Evaluation"}
      </button>

      {error && (
        <p className="error" style={{ marginTop: "0.6rem" }}>
          <IconAlert /> {error}
        </p>
      )}

      {report && (
        <div className="card evaluation-report" style={{ marginTop: "0.8rem" }}>
          <div className="stat-row">
            <span>Confirmed / failed / unreviewed</span>
            <strong>
              {report.track_counts.confirmed} / {report.track_counts.failed} / {report.track_counts.unreviewed}
            </strong>
          </div>
          <div className="stat-row">
            <span>Detection recall</span>
            <strong>{report.detection_recall === null ? "n/a" : `${(report.detection_recall * 100).toFixed(0)}%`}</strong>
          </div>
          {report.detection_recall === null && (
            <p style={{ color: "var(--text-muted)", fontSize: "0.85em", margin: "-0.2rem 0 0" }}>
              Freeze this source with a ground-truth count (Sources tab) to measure recall.
            </p>
          )}
          <div className="stat-row">
            <span>Duplicate / fragmented pairs</span>
            <strong>
              {report.duplicate_track_pairs.length} / {report.fragmented_track_pairs.length}
            </strong>
          </div>
          <div className="stat-row">
            <span>OCR agreement</span>
            <strong>
              {report.ocr_metrics.agreement_rate === null
                ? "n/a"
                : `${(report.ocr_metrics.agreement_rate * 100).toFixed(0)}% (${report.ocr_metrics.tracks_with_ocr})`}
            </strong>
          </div>

          <div className="section-title" style={{ marginTop: "0.3rem" }}>
            Class distribution (this run's tracks)
          </div>
          <ul className="class-distribution-list">
            {Object.entries(report.class_distribution).map(([name, count]) => (
              <li key={name}>
                <span>{name}</span>
                <strong>{count}</strong>
              </li>
            ))}
            {/* Scoped to one processing run, so a box drawn on the
                labelling canvas is not in it and cannot be - the frame's
                source may have several runs. "None yet." read as "you
                have not labelled anything", which for a canvas labeller
                was flatly wrong. */}
            {Object.keys(report.class_distribution).length === 0 && (
              <li className="empty">No track reviewed in this run. Canvas labels are counted under "Your labels".</li>
            )}
          </ul>

          <div className="section-title" style={{ marginTop: "0.3rem" }}>
            Failure gallery ({report.failure_gallery.length})
          </div>
          <div className="failure-gallery">
            {report.failure_gallery.map((item) => (
              <img
                key={item.track_id}
                src={item.representative_frame_id ? api.frameImageUrl(item.representative_frame_id) : undefined}
                alt={item.track_id}
                title={`${item.bucket ?? "?"} / ${item.review_status}`}
              />
            ))}
            {report.failure_gallery.length === 0 && <span className="empty">None.</span>}
          </div>
        </div>
      )}
    </div>
  );
}

export default EvaluationPanel;
