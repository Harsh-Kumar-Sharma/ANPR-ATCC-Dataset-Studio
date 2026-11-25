import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { IconAlert, IconCheck, IconFilm, IconPencil, IconPlay, IconX } from "../Icons";
import ModelPicker, { useModelChoice } from "./ModelPicker";
import ProgressBar from "./ProgressBar";
import { sourceLabel, sourceOrigin } from "../sourceLabel";
import type { DatasetImportResult, Project, RunEstimate, Source, SourceContents } from "../types";

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

/** One thing to import. ``run`` reports upload progress where there is
 *  an upload, and resolves to the line that says how it went. */
interface ImportJob {
  name: string;
  /** Bytes to send; 0 for a path on the server, which sends nothing. */
  size: number;
  run: (onProgress: (sent: number, total: number) => void) => Promise<string>;
}

/** What an imported dataset became, and what to look at first. */
function describeImport(result: DatasetImportResult): string {
  const parts = [
    `${sourceLabel(result.source)}: ${result.images_imported} image(s), ${result.boxes_imported} box(es) from the model`,
  ];
  if (result.repeated_spot_frames > 0) {
    parts.push(`${result.repeated_spot_frames} with a box where boxes sit in many pictures - check these first`);
  }
  if (result.low_confidence_frames > 0) parts.push(`${result.low_confidence_frames} the model was unsure of`);
  if (result.already_imported > 0) parts.push(`${result.already_imported} already here, skipped`);
  if (result.unreadable > 0) parts.push(`${result.unreadable} would not open`);
  return parts.join(" · ");
}

function SourcePanel({ project, onProcessed, onWatch, onLabel, onRemoved }: Props) {
  const [sources, setSources] = useState<Source[]>([]);
  const [path, setPath] = useState("");
  const [datasetPath, setDatasetPath] = useState("");
  // One import at a time: they all write to the same project, and one
  // progress line is all that fits.
  const [importing, setImporting] = useState(false);
  const [uploading, setUploading] = useState<{ name: string; sent: number; total: number } | null>(null);
  const videoFiles = useRef<HTMLInputElement>(null);
  const datasetFiles = useRef<HTMLInputElement>(null);
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
  const confirmRef = useRef<HTMLDivElement>(null);

  // Brought into view when it opens, wherever the list was scrolled.
  useEffect(() => {
    if (doomed) confirmRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [doomed]);
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

  /** Import one thing after another - files chosen on this computer, or
   *  a path on the server. One that fails does not stop the rest; each
   *  outcome is named after its file. The sources list refreshes after
   *  each, so a long batch shows its progress as it goes. */
  async function runImports(jobs: ImportJob[]) {
    if (jobs.length === 0) return;
    setError(null);
    setStatus(null);
    setImporting(true);
    const imported: string[] = [];
    const failed: string[] = [];
    try {
      for (const job of jobs) {
        setUploading({ name: job.name, sent: 0, total: job.size });
        try {
          imported.push(await job.run((sent, total) => setUploading({ name: job.name, sent, total })));
        } catch (e) {
          failed.push(`${job.name}: ${e instanceof ApiError ? e.message : String(e)}`);
        }
        refresh();
      }
    } finally {
      setUploading(null);
      setImporting(false);
    }
    if (imported.length > 0) setStatus(imported.join(" | "));
    if (failed.length > 0) setError(failed.join(" | "));
  }

  function handleVideoFiles(files: File[]) {
    return runImports(
      files.map((file) => ({
        name: file.name,
        size: file.size,
        run: async (onProgress) => `${sourceLabel(await api.uploadSource(project.id, file, onProgress))}: imported`,
      })),
    );
  }

  function handleDatasetFiles(files: File[]) {
    return runImports(
      files.map((file) => ({
        name: file.name,
        size: file.size,
        run: async (onProgress) => describeImport(await api.uploadDataset(project.id, file, onProgress)),
      })),
    );
  }

  async function handleDatasetPath(e: React.FormEvent) {
    e.preventDefault();
    const typed = datasetPath.trim();
    if (!typed) return;
    await runImports([
      { name: typed, size: 0, run: async () => describeImport(await api.importDataset(project.id, typed)) },
    ]);
    setDatasetPath("");
  }

  /** The files a file input was given, cleared afterwards so choosing
   *  the same file again still counts as a change. */
  function takeFiles(e: React.ChangeEvent<HTMLInputElement>): File[] {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    return files;
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

      {/* Choosing a file on this computer is the way in from a browser.
          A path is for footage already on the server - the incoming
          folder - where uploading it again would be a waste. */}
      <div className="import-group">
        <span className="import-group__title">Video - frames are taken from it</span>
        <button
          type="button"
          className="btn-primary import-group__choose"
          disabled={importing}
          title="Pick one or more videos (mp4, avi, mkv, mov) from this computer"
          onClick={() => videoFiles.current?.click()}
        >
          <IconFilm /> Choose video file…
        </button>
        <input
          ref={videoFiles}
          type="file"
          accept="video/*,.mp4,.avi,.mkv,.mov"
          multiple
          hidden
          data-testid="video-files"
          onChange={(e) => handleVideoFiles(takeFiles(e))}
        />
        <form onSubmit={handleImport} className="import-form">
          <input
            type="text"
            placeholder="…or a video path on the server"
            aria-label="Video path on the server"
            value={path}
            onChange={(e) => setPath(e.target.value)}
          />
          <button type="submit" disabled={!path.trim() || importing}>
            Import
          </button>
        </form>
      </div>

      {/* A production node's YOLO export: its images arrive with the
          model's boxes on them, waiting in the label queue for review. */}
      <div className="import-group">
        <span className="import-group__title">Dataset - a YOLO .zip exported by production</span>
        <button
          type="button"
          className="btn-primary import-group__choose"
          disabled={importing}
          title="Pick one or more dataset .zip files from this computer"
          onClick={() => datasetFiles.current?.click()}
        >
          <IconFilm /> Choose .zip file(s)…
        </button>
        <input
          ref={datasetFiles}
          type="file"
          accept=".zip,application/zip"
          multiple
          hidden
          data-testid="dataset-files"
          onChange={(e) => handleDatasetFiles(takeFiles(e))}
        />
        <form onSubmit={handleDatasetPath} className="import-form">
          <input
            type="text"
            placeholder="…or a .zip or folder path on the server"
            aria-label="Dataset path on the server"
            value={datasetPath}
            onChange={(e) => setDatasetPath(e.target.value)}
          />
          <button type="submit" disabled={!datasetPath.trim() || importing}>
            Import dataset
          </button>
        </form>
      </div>

      {uploading && (
        <div className="import-progress" role="status">
          <span>
            {uploading.total > 0 && uploading.sent < uploading.total
              ? `Uploading ${uploading.name} - ${Math.floor((uploading.sent / uploading.total) * 100)}% ` +
                `(${readableSize(uploading.sent)} of ${readableSize(uploading.total)})`
              : `Importing ${uploading.name}…`}
          </span>
          {uploading.total > 0 && (
            <ProgressBar
              fraction={uploading.sent / uploading.total}
              label={`Upload of ${uploading.name}`}
              className="import-progress__bar"
            />
          )}
        </div>
      )}

      {/* While a removal is being asked about, its own box says it. */}
      {error && !doomed && (
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
                : s.type === "dataset"
                  ? `imported dataset · ${s.stored_frames} image(s) · ${s.width}x${s.height}`
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
            {/* A validation clip is counted by watching the video; an
                imported dataset has no video to watch. */}
            {s.type === "dataset" ? null : s.is_frozen ? (
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
            {/* Under the source it is about, not after the whole list:
                with a dozen sources it was opening off-screen, and the
                cross looked as if it did nothing. */}
            {doomed?.id === s.id && (
      <div className="delete-confirm source-remove-confirm" ref={confirmRef}>
                <p className="delete-confirm-title">
                  Remove <strong>{sourceLabel(doomed)}</strong>?
                </p>
                {error && (
                  <p className="error">
                    <IconAlert /> {error}
                  </p>
                )}
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
          </li>
        ))}
        {sources.length === 0 && <li className="empty">No sources imported yet.</li>}
      </ul>

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
