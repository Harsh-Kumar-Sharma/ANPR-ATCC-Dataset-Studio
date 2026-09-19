import { useCallback, useEffect, useState } from "react";
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
              onChange={(e) => setImageSize(Math.max(32, Number(e.target.value)))}
            />
          </label>

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
              {typeof run.settings_json?.note === "string" && (
                <span className="training-runs__caveat">{run.settings_json.note}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default TrainingPanel;
