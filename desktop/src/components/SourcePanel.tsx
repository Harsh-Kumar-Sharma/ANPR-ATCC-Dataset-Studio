import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { IconAlert, IconCheck, IconFilm, IconPencil, IconPlay, IconX } from "../Icons";
import ModelPicker, { useModelChoice } from "./ModelPicker";
import { sourceLabel, sourceOrigin } from "../sourceLabel";
import type { Project, RunEstimate, Source, SourceContents } from "../types";

interface Props {
  project: Project;
  onProcessed: () => void;
  onWatch: (source: Source) => void;
  /** Open the Label tab already narrowed to this source. */
  onLabel?: (source: Source) => void;
  /** A source and everything derived from it has gone. Told so the
   *  rest of the app can stop showing what no longer exists. */
  onRemoved?: (sourceId: string) => void;
}

/** Bytes as something a person can weigh a decision against. */
function readableSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "an unknown amount";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  let shown = Number(value.toFixed(value < 10 ? 1 : 0));
  if (shown >= 1024 && unit < units.length - 1) {
    shown = 1;
    unit += 1;
  }
  return `${shown.toFixed(shown < 10 ? 1 : 0)} ${units[unit]}`;
}

function SourcePanel({ project, onProcessed, onWatch, onLabel, onRemoved }: Props) {
  const [sources, setSources] = useState<Source[]>([]);
  const [path, setPath] = useState("");
  const [targetFps, setTargetFps] = useState(5);
  // Which model detects. Remembered per project: a project is one
  // kind of footage, and what suited it last time suits it now.
  const modelChoice = useModelChoice(project.id);
  // Walk the whole clip instead of sampling it. The expensive option,
  // so what it costs is shown before it is taken.
  const [everyFrame, setEveryFrame] = useState(false);
  const [estimate, setEstimate] = useState<RunEstimate | null>(null);
  const [estimateFor, setEstimateFor] = useState<string | null>(null);
  const [busySourceId, setBusySourceId] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [groundTruthDrafts, setGroundTruthDrafts] = useState<Record<string, string>>({});
  /** The source the user is being asked to confirm removing, if any. */
  const [doomed, setDoomed] = useState<Source | null>(null);
  const [contents, setContents] = useState<SourceContents | null>(null);
  const [removing, setRemoving] = useState(false);
  /** The source being renamed, and the name typed so far. */
  const [naming, setNaming] = useState<{ id: string; draft: string } | null>(null);
  const [savingName, setSavingName] = useState(false);

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

  async function askToRemove(source: Source) {
    setDoomed(source);
    setContents(null);
    setError(null);
    setStatus(null);
    try {
      const loaded = await api.getSourceContents(project.id, source.id);
      // Only if this is still the source being asked about - clicking
      // one then another before the first reply lands would otherwise
      // show the first one's counts under the second one's name.
      setDoomed((current) => {
        if (current?.id === source.id) setContents(loaded);
        return current;
      });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function confirmRemove() {
    if (!doomed) return;
    setRemoving(true);
    setError(null);
    try {
      const removed = await api.deleteSource(project.id, doomed.id);
      setSources((previous) => previous.filter((s) => s.id !== doomed.id));
      setStatus(
        `Removed ${sourceLabel(doomed)}: ${removed.labels} label(s), ${removed.frames} frame(s).` +
          (removed.files_removed ? "" : " Its files were left on disk - remove that folder by hand."),
      );
      setDoomed(null);
      setContents(null);
      // The tracks and frames went with it. Anything still showing
      // them is showing rows that are not there any more - clicking
      // one answers "Track not found", which is how this was found.
      onRemoved?.(doomed.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setRemoving(false);
    }
  }

  const removeBusy = (contents?.running_jobs ?? 0) > 0;

  async function saveName() {
    if (!naming) return;
    setSavingName(true);
    setError(null);
    try {
      const renamed = await api.renameSource(project.id, naming.id, naming.draft);
      // Only the name changed; keep the counts the list already has.
      setSources((previous) => previous.map((s) => (s.id === renamed.id ? { ...s, name: renamed.name } : s)));
      setNaming(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSavingName(false);
    }
  }

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

  /** What this run would cost, asked when the settings change.
   *
   *  Shown rather than enforced here: the backend refuses a run that
   *  will not fit, and a second opinion in the UI would be one more
   *  thing to keep in step with it. */
  async function askForEstimate(source: Source) {
    setEstimateFor(source.id);
    setEstimate(null);
    try {
      setEstimate(await api.estimateRun(project.id, source.id, targetFps, everyFrame));
    } catch {
      // An estimate that cannot be had is not worth an error message:
      // the run itself still reports honestly.
      setEstimate(null);
    }
  }

  async function handleProcess(source: Source) {
    setBusySourceId(source.id);
    setStatus(null);
    setError(null);
    try {
      // Returns as soon as the run is queued - there are no tracks to
      // count yet. Progress is followed in the jobs panel and the
      // global indicator; this tab just confirms the hand-off.
      await api.processSource(project.id, source.id, targetFps, modelChoice.modelId, everyFrame);
      setStatus(`${source.path_or_uri.split(/[\\/]/).pop()}: queued. Follow it under Jobs.`);
      refresh();
      onProcessed();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusySourceId(null);
    }
  }

  async function handleChooseFrames(source: Source) {
    setBusySourceId(source.id);
    setStatus(null);
    setError(null);
    try {
      // Decoding every sampled frame takes minutes, so this returns as
      // soon as the work is queued; the jobs panel carries it from here.
      await api.selectFrames(project.id, source.id);
      setStatus(`${source.path_or_uri.split(/[\\/]/).pop()}: choosing frames, queued. Follow it under Jobs.`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
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
            {/* The name is the way into this source's frames: not
                knowing which frames came from which clip is the
                complaint this answers. */}
            {naming?.id === s.id ? (
              <span className="source-rename">
                <input
                  type="text"
                  aria-label={`Name for ${sourceLabel(s)}`}
                  autoFocus
                  maxLength={128}
                  value={naming.draft}
                  placeholder={sourceOrigin(s)}
                  onChange={(e) => setNaming({ id: s.id, draft: e.target.value })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") saveName();
                    if (e.key === "Escape") setNaming(null);
                  }}
                />
                <button className="btn-primary" disabled={savingName} onClick={saveName}>
                  Save
                </button>
                <button onClick={() => setNaming(null)}>Cancel</button>
              </span>
            ) : (
              <span className="source-title">
                <button
                  className="source-name source-name--link"
                  title={`Label the frames from ${sourceLabel(s)}`}
                  onClick={() => onLabel?.(s)}
                >
                  {sourceLabel(s)}
                </button>
                <button
                  className="source-rename__start"
                  aria-label={`Rename ${sourceLabel(s)}`}
                  title="Give this source a name of its own"
                  onClick={() => setNaming({ id: s.id, draft: s.name ?? "" })}
                >
                  <IconPencil />
                </button>
              </span>
            )}
            {s.name && <span className="source-origin">{sourceOrigin(s)}</span>}
            <button
              className="source-remove"
              data-testid="remove-source"
              aria-label={`Remove ${sourceLabel(s)}`}
              title="Remove this source and everything found in it"
              onClick={() => askToRemove(s)}
            >
              <IconX />
            </button>
            <span className="meta">
              {/* A live stream has no known length, so frame_count is a
                  0 sentinel and saying "0 frames" for one holding
                  three hundred is how empty sources became hard to
                  tell from full ones. */}
              {s.type === "rtsp"
                ? `live · ${s.fps}fps · ${s.stored_frames} frame(s) saved`
                : `${s.width}x${s.height} @ ${s.fps}fps · ${s.frame_count} frames`}
            </span>
            <div className="source-actions">
              {s.type === "video" && (
                <button onClick={() => onWatch(s)} title="Play this video with detection boxes">
                  <IconFilm /> Watch
                </button>
              )}
              {/* Offline video only. A live stream has no file to decode
                  and no frame count, so this could only ever fail -
                  which is exactly what it did, with "frame_count must
                  be positive", on both RTSP sources in a real project.
                  Live capture is the Live tab's job. */}
              {s.type === "video" && (
                <button
                  disabled={busySourceId === s.id || s.is_processing}
                  onClick={() => handleProcess(s)}
                  onMouseEnter={() => askForEstimate(s)}
                  onFocus={() => askForEstimate(s)}
                >
                  <IconPlay />
                  {busySourceId === s.id || s.is_processing ? "Processing…" : "Detect + Track"}
                </button>
              )}
              {s.type === "video" && (
                <button
                  disabled={busySourceId === s.id || s.is_processing}
                  onClick={() => handleChooseFrames(s)}
                  title="Set aside near-duplicate and poor frames so the labelling queue is worth working through"
                >
                  Choose frames
                </button>
              )}
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

      {doomed && (
        <div className="delete-confirm">
          <p className="delete-confirm-title">
            Remove <strong>{sourceLabel(doomed)}</strong>?
          </p>
          {contents === null ? (
            <p className="empty">{error ? "Could not read what is in this source." : "Working out what is in it…"}</p>
          ) : (
            <>
              <p className="delete-summary" data-testid="remove-source-summary">
                This destroys {contents.labels} label(s), {contents.tracks} track(s), {contents.frames} frame(s) and{" "}
                {readableSize(contents.bytes)} of files. Datasets you have already exported are not touched. It cannot
                be undone.
              </p>
              {removeBusy && (
                <p className="error">
                  <IconAlert /> {contents.running_jobs} unfinished job(s) or live capture(s) against this source. Wait
                  for them to finish, cancel them, or stop the capture first.
                </p>
              )}
            </>
          )}
          <div className="delete-confirm-actions">
            <button onClick={() => { setDoomed(null); setContents(null); }}>Cancel</button>
            <button
              className="btn-danger"
              disabled={contents === null || removeBusy || removing}
              onClick={confirmRemove}
            >
              {removing ? "Removing…" : "Remove this source"}
            </button>
          </div>
        </div>
      )}

      <ModelPicker choice={modelChoice} id="detect-model" />

      <label className="every-frame">
        <input
          type="checkbox"
          checked={everyFrame}
          onChange={(e) => {
            setEveryFrame(e.target.checked);
            setEstimate(null);
          }}
        />
        Every frame with a vehicle
      </label>
      <p className="every-frame__note">
        {everyFrame
          ? "Walks the whole clip at its own rate and keeps every frame the model finds something in."
          : "Samples at the rate below. Faster, and enough for a survey."}
      </p>

      {/* The numbers, before the button rather than at 80% with the
          disk full. */}
      {estimate && estimateFor && (
        <p className={`run-estimate${estimate.fits ? "" : " run-estimate--no"}`} data-testid="run-estimate">
          About {estimate.frames_to_process.toLocaleString()} frames &middot; {readableSize(estimate.bytes_now)}{" "}
          now, up to {readableSize(estimate.bytes_if_every_frame_reviewed)} if you label all of it &middot;{" "}
          {readableSize(estimate.free_bytes)} free
          {!estimate.fits && estimate.reason ? ` — ${estimate.reason}` : ""}
        </p>
      )}

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
