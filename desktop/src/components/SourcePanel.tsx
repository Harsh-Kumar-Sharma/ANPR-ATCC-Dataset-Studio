import { useEffect, useState } from "react";
import { api } from "../api";
import { IconAlert, IconCheck, IconFilm, IconPlay } from "../Icons";
import type { Project, Source } from "../types";

interface Props {
  project: Project;
  onProcessed: () => void;
}

function SourcePanel({ project, onProcessed }: Props) {
  const [sources, setSources] = useState<Source[]>([]);
  const [path, setPath] = useState("");
  const [targetFps, setTargetFps] = useState(5);
  const [busySourceId, setBusySourceId] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [groundTruthDrafts, setGroundTruthDrafts] = useState<Record<string, string>>({});

  function refresh() {
    api
      .listSources(project.id)
      .then(setSources)
      .catch((e) => setError(String(e)));
  }

  useEffect(refresh, [project.id]);

  // A real detect+track run can take 1-3 minutes on real footage (see
  // docs/HANDOFF.md). Poll while any source is still processing so the
  // "Processing..." state stays accurate even after a reload or from a
  // second window, instead of relying only on this tab's own click state.
  useEffect(() => {
    if (!sources.some((s) => s.is_processing)) return;
    const timer = setTimeout(refresh, 3000);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sources]);

  async function handleImport(e: React.FormEvent) {
    e.preventDefault();
    if (!path.trim()) return;
    setError(null);
    try {
      await api.importSource(project.id, path.trim());
      setPath("");
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleProcess(source: Source) {
    setBusySourceId(source.id);
    setStatus(null);
    setError(null);
    try {
      const result = await api.processSource(project.id, source.id, targetFps);
      setStatus(`${source.path_or_uri.split(/[\\/]/).pop()}: ${result.tracks.length} track(s) found.`);
      onProcessed();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusySourceId(null);
    }
  }

  async function handleFreeze(source: Source) {
    const count = Number(groundTruthDrafts[source.id]);
    if (!count || count <= 0) {
      setError("Enter how many real vehicles are actually in this clip first.");
      return;
    }
    try {
      await api.freezeSource(project.id, source.id, count);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="source-panel">
      <div className="section-title">
        <IconFilm /> Sources
      </div>

      <form onSubmit={handleImport} className="import-form">
        <input
          type="text"
          placeholder="Local video path (mp4/avi/mkv)"
          value={path}
          onChange={(e) => setPath(e.target.value)}
        />
        <button type="submit" className="btn-primary" disabled={!path.trim()}>
          Import
        </button>
      </form>

      {error && (
        <p className="error" style={{ marginBottom: "0.6rem" }}>
          <IconAlert /> {error}
        </p>
      )}
      {status && (
        <p className="status" style={{ marginBottom: "0.6rem" }}>
          <IconCheck /> {status}
        </p>
      )}

      <ul className="source-list">
        {sources.map((s) => (
          <li key={s.id}>
            <span className="source-name" title={s.path_or_uri}>
              {s.path_or_uri.split(/[\\/]/).pop()}
            </span>
            <span className="meta">
              {s.width}x{s.height} @ {s.fps}fps · {s.frame_count} frames
            </span>
            <div className="source-actions">
              <button disabled={busySourceId === s.id || s.is_processing} onClick={() => handleProcess(s)}>
                <IconPlay />
                {busySourceId === s.id || s.is_processing ? "Processing…" : "Detect + Track"}
              </button>
            </div>
            {s.is_frozen ? (
              <span className="frozen-badge">
                <IconCheck /> Frozen · {s.ground_truth_vehicle_count} real vehicles
              </span>
            ) : (
              <span className="freeze-control">
                <input
                  type="number"
                  min={1}
                  placeholder="ground truth #"
                  value={groundTruthDrafts[s.id] ?? ""}
                  onChange={(e) => setGroundTruthDrafts((prev) => ({ ...prev, [s.id]: e.target.value }))}
                />
                <button onClick={() => handleFreeze(s)}>Freeze as validation clip</button>
              </span>
            )}
          </li>
        ))}
        {sources.length === 0 && <li className="empty">No sources imported yet.</li>}
      </ul>

      <label className="fps-control">
        Sampling FPS
        <input
          type="number"
          min={0.1}
          step={0.5}
          value={targetFps}
          onChange={(e) => setTargetFps(Number(e.target.value))}
        />
      </label>
    </div>
  );
}

export default SourcePanel;
