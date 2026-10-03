import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import ModelPicker, { useModelChoice } from "./ModelPicker";
import type { DatasetVersion, Job, Project, TrainingRun } from "../types";

interface Props {
  project: Project;
  /** The jobs already being polled, so this does not poll again. */
  jobs: Job[];
  /** Bumped when a version is exported, so the list catches up. */
  refreshKey?: number;
}

interface Scores {
  precision: number;
  recall: number;
  map50: number;
  map50_95: number;
  images: number;
}

/** Recall below this misses plates a toll cannot afford to miss. */
const RECALL_TARGET = 0.97;

const SUBSET_LABEL: Record<string, string> = { all: "Test", day: "Day", night: "Night" };

/** The trained model's scores on test images it never saw - overall,
 *  then by day and night when the export marks night shots. */
function TestScores({ metrics }: { metrics: unknown }) {
  if (!metrics || typeof metrics !== "object") return null;
  const m = metrics as Record<string, unknown>;
  if (typeof m.error === "string") return <span className="training-runs__caveat">{m.error}</span>;
  if (typeof m.note === "string") return <span className="training-runs__caveat">{m.note}</span>;
  const rows = ["all", "day", "night"].filter((k) => m[k] && typeof m[k] === "object");
  if (rows.length === 0) return null;
  return (
    <span className="training-scores" data-testid="test-scores">
      {rows.map((key) => {
        const s = m[key] as Scores;
        const low = s.recall < RECALL_TARGET;
        return (
          <span key={key} className="training-scores__row">
            <strong>{SUBSET_LABEL[key] ?? key}</strong> ({s.images} img) · P {s.precision.toFixed(2)} ·{" "}
            <span className={low ? "training-scores__low" : ""} title={`Target ${RECALL_TARGET} or above`}>
              R {s.recall.toFixed(2)}
            </span>{" "}
            · mAP50 {s.map50.toFixed(2)} · mAP50-95 {s.map50_95.toFixed(2)}
          </span>
        );
      })}
    </span>
  );
}

/** How a finished run reads at a glance. */
function describe(run: TrainingRun): string {
  if (run.status === "completed") {
    const score = run.best_map50 === null ? "" : ` · mAP50 ${run.best_map50.toFixed(3)}`;
    return `Finished${score}`;
  }
  if (run.status === "running") return `Epoch ${run.last_epoch ?? 0} of ${run.epochs}`;
  if (run.status === "pending") return "Waiting to start";
  return run.error_message ?? run.status;
}

/**
 * Training, without leaving the app.
 *
 * The app used to write a data.yaml and a RETRAINING.md and stop
 * there, leaving a command to copy into a terminal. This starts that
 * command and follows it.
 */
function TrainingPanel({ project, jobs, refreshKey = 0 }: Props) {
  const [versions, setVersions] = useState<DatasetVersion[]>([]);
  const [versionId, setVersionId] = useState<string>("");
  const [epochs, setEpochs] = useState(100);
  const [imageSize, setImageSize] = useState(640);
  // A plate is a few dozen pixels of a 1920-wide frame; at 640 the far
  // ones are a handful. Plate projects start at 960 instead.
  const [platesProject, setPlatesProject] = useState(false);
  const sizeTouched = useRef(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getClassSchema(project.id)
      .then((classes) => {
        if (cancelled) return;
        const plates = classes.some((c) => c.name.toLowerCase().includes("plate"));
        setPlatesProject(plates);
        if (plates && !sizeTouched.current) setImageSize(960);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [project.id]);
  const [runs, setRuns] = useState<TrainingRun[]>([]);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The model to start from. A built-in, or one trained here before -
  // which is the loop this whole app exists to serve.
  const baseModel = useModelChoice(project.id);

  const refresh = useCallback(async () => {
    // Fetched separately on purpose. These two failures mean
    // different things, and one taking the other down with it turned
    // "no training runs yet" into a panel with nothing in it at all.
    try {
      const exported = await api.listDatasetVersions(project.id);
      setVersions(exported);
      setVersionId((current) => current || exported[0]?.id || "");
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }

    try {
      setRuns(await api.listTrainingRuns(project.id));
    } catch {
      // No history is not an error worth a message: there may
      // genuinely be none, and the panel still works without it.
      setRuns([]);
    }
  }, [project.id]);

  useEffect(() => {
    refresh();
  }, [refresh, refreshKey]);

  // A training job finishing is what turns a run into a model, and it
  // happens minutes to hours after the click that started it - so the
  // list reacts to the job going terminal, not to the request
  // returning. Same shape the track list uses for detection.
  const finishedTraining = jobs.filter((j) => j.type === "train" && j.status !== "running").length;
  useEffect(() => {
    refresh();
  }, [finishedTraining, refresh]);

  const active = runs.find((r) => r.status === "running" || r.status === "pending") ?? null;
  const finished = runs.filter((r) => r.id !== active?.id);
  const activeJob = active ? jobs.find((j) => j.id === active.job_id) ?? null : null;

  /** Both of these take the row and nothing else: a model the run
   *  trained is in the models directory and may be in use, and its
   *  checkpoints are on disk. Clearing a failure off a screen should
   *  not delete a model. */
  async function forget(run: TrainingRun) {
    setError(null);
    try {
      await api.forgetTrainingRun(run.id);
      setRuns((previous) => previous.filter((r) => r.id !== run.id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function clearFinished() {
    setError(null);
    try {
      await api.clearFinishedTrainingRuns(project.id);
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function start() {
    if (!versionId) return;
    setStarting(true);
    setError(null);
    try {
      await api.startTraining(project.id, {
        datasetVersionId: versionId,
        baseModelId: baseModel.modelId ?? "",
        epochs,
        imageSize,
      });
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  }

  return (
    <section className="training-panel">
      <h3>Training</h3>

      {versions.length === 0 ? (
        <p className="empty">Export a dataset version first - that is what training runs on.</p>
      ) : (
        <>
          <label className="training-field">
            <span>Dataset</span>
            <select
              aria-label="Dataset version to train on"
              value={versionId}
              onChange={(e) => setVersionId(e.target.value)}
            >
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version}
                </option>
              ))}
            </select>
          </label>

          {/* Starting from a model trained here before is the whole
              loop: label, train, detect, label what it got wrong. */}
          <ModelPicker choice={baseModel} id="base-model" label="Start from" manage={false} />

          <label className="training-field">
            <span>Epochs</span>
            <input
              type="number"
              min={1}
              value={epochs}
              onChange={(e) => setEpochs(Math.max(1, Number(e.target.value)))}
            />
          </label>
          <label className="training-field">
            <span>Image size</span>
            <input
              type="number"
              min={32}
              step={32}
              value={imageSize}
              onChange={(e) => {
                sizeTouched.current = true;
                setImageSize(Math.max(32, Number(e.target.value)));
              }}
            />
          </label>
          {platesProject && (
            <p className="training-note">
              Number plates: trained at {imageSize} without mirrored images (a flipped plate does not exist), little
              rotation, and stopping early once it stops improving. Detection uses the size the model was trained at.
              If the GPU runs out of memory at 960, lower the batch on the server (ANPR_TRAIN_BATCH).
            </p>
          )}

          <button
            className="btn-primary btn-block"
            disabled={starting || active !== null || !versionId || !baseModel.modelId}
            onClick={start}
          >
            {active ? "A run is already going" : starting ? "Starting…" : "Train"}
          </button>
          <p className="training-note">
            One run at a time: this machine has one GPU, and a second would make both slower. Training
            keeps going if you close the app.
          </p>
        </>
      )}

      {error && <p className="error">{error}</p>}

      {active && (
        <div className="training-active" role="status">
          <strong>{describe(active)}</strong>
          {activeJob?.progress_message && <p className="training-note">{activeJob.progress_message}</p>}
        </div>
      )}

      {finished.length > 0 && (
        <div className="training-runs__header">
          <span>Past runs</span>
          {/* Three failures from three attempts at the same bug is a
              list nobody wants to clear one line at a time. */}
          <button onClick={clearFinished}>Clear all</button>
        </div>
      )}

      {/* The run in progress has its own block above; repeating it
          here would print the same epoch twice, one line apart. */}
      {finished.length > 0 && (
        <ul className="training-runs">
          {finished.map((run) => (
            <li key={run.id}>
              <span className="training-runs__what">
                v{versions.find((v) => v.id === run.dataset_version_id)?.version ?? "?"} from{" "}
                {run.base_model_id}
              </span>
              <span className={`training-runs__status training-runs__status--${run.status}`}>
                {describe(run)}
              </span>
              {run.output_model_id && (
                <span className="training-runs__model">Added as {run.output_model_id}</span>
              )}
              {/* Said next to the score, not buried: an mAP measured
                  on the training images is not a measure of anything,
                  and looks identical to one that is. */}
              <TestScores metrics={run.settings_json?.test_metrics} />
              {typeof run.settings_json?.note === "string" && (
                <span className="training-runs__caveat">{run.settings_json.note}</span>
              )}
              <button
                className="training-runs__forget"
                aria-label={`Remove this run from the list`}
                onClick={() => forget(run)}
              >
                Remove from list
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default TrainingPanel;
