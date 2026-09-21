import { useEffect, useState } from "react";
import { api } from "../api";
import { IconAlert, IconBox, IconCheck } from "../Icons";
import { readableSize } from "./StoragePanel";
import type { DatasetVersion, Project } from "../types";

interface Props {
  project: Project;
}

function DatasetPanel({ project }: Props) {
  const [versions, setVersions] = useState<DatasetVersion[]>([]);
  const [exporting, setExporting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [handoffVersionId, setHandoffVersionId] = useState<string | null>(null);
  const [handoffContent, setHandoffContent] = useState<string | null>(null);

  function refresh() {
    api.listDatasetVersions(project.id).then(setVersions).catch(() => undefined);
  }

  useEffect(refresh, [project.id]);

  async function handleExport() {
    setExporting(true);
    setError(null);
    setMessage(null);
    setWarnings([]);
    try {
      const result = await api.exportDataset(project.id);
      const warnings = result.validation.warnings ?? [];
      // Images and boxes are different numbers - a frame can carry many
      // boxes - and a labeller wants to know both went in. The empty
      // frames are *of* those images, not extra ones, so they are
      // phrased as a subset; reading them as an addition would make the
      // dataset look bigger than it is.
      const background = result.background_frames ?? 0;
      const dropped = result.frames_skipped_unclassified ?? 0;
      const lost = result.frames_skipped_unrecoverable ?? 0;
      setMessage(
        `v${result.dataset_version.version}: ${result.counts.total} full frame(s)` +
          (background ? ` (${background} labelled empty)` : "") +
          `, ${result.object_counts?.total ?? 0} box(es)` +
          `, validation ${result.validation.valid ? "passed" : "FAILED"}.` +
          // Work that did not make it in. Saying nothing here is how a
          // user ends up wondering why they labelled fifty frames and
          // exported forty-five.
          (dropped ? ` ${dropped} labelled frame(s) left out - no class on their boxes yet.` : "") +
          // Different from the line above, and worth its own: this
          // work cannot be finished by going back and classifying a
          // box. The pixels are gone.
          (lost ? ` ${lost} labelled frame(s) left out - their image is gone.` : "") +
          (warnings.length ? ` ${warnings.length} warning(s).` : ""),
      );
      setWarnings(warnings);
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setExporting(false);
    }
  }

  async function handleRetrainingHandoff(version: DatasetVersion) {
    setError(null);
    try {
      const result = await api.createRetrainingHandoff(version.id);
      setHandoffVersionId(version.id);
      setHandoffContent(result.instructions_content);
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="dataset-panel">
      <div className="section-title">
        <IconBox /> Dataset
      </div>

      <button className="btn-primary btn-block" disabled={exporting} onClick={handleExport}>
        {exporting ? "Exporting…" : "Export Dataset Version"}
      </button>

      {error && (
        <p className="error" style={{ marginTop: "0.6rem" }}>
          <IconAlert /> {error}
        </p>
      )}
      {message && (
        <p className="status" style={{ marginTop: "0.6rem" }}>
          <IconCheck /> {message}
        </p>
      )}
      {warnings.map((w) => (
        <p key={w} className="warning" style={{ marginTop: "0.4rem" }}>
          <IconAlert /> {w}
        </p>
      ))}

      <ul className="dataset-version-list">
        {versions.map((v) => (
          <li key={v.id}>
            <span>
              v{v.version} · seed {v.split_seed}
              {/* What it weighs, so the click below is an informed one. */}
              {v.bytes_on_disk > 0 && <em className="dataset-version-size"> · {readableSize(v.bytes_on_disk)}</em>}
            </span>
            {/* A plain link, not a fetch: the server streams the zip and
                the browser writes it straight to disk, so nothing holds
                the whole dataset in memory on the way past. */}
            <a className="button" href={api.datasetArchiveUrl(v.id)} download>
              Download ZIP
            </a>
            <button onClick={() => handleRetrainingHandoff(v)}>Retraining handoff</button>
          </li>
        ))}
        {versions.length === 0 && <li className="empty">No exports yet.</li>}
      </ul>

      {handoffContent && (
        <pre className="handoff-instructions">
          {`v${versions.find((v) => v.id === handoffVersionId)?.version}:\n${handoffContent}`}
        </pre>
      )}
    </div>
  );
}

export default DatasetPanel;
