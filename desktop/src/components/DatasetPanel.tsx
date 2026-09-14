import { useEffect, useState } from "react";
import { api } from "../api";
import { IconAlert, IconBox, IconCheck } from "../Icons";
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
      setMessage(
        `v${result.dataset_version.version}: ${result.counts.total} full frame(s), ` +
          `validation ${result.validation.valid ? "passed" : "FAILED"}.` +
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
            </span>
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
