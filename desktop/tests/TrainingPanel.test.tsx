/**
 * Training without leaving the app.
 *
 * The app used to write a data.yaml and a RETRAINING.md and stop
 * there, leaving a command to copy into a terminal.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api";
import TrainingPanel from "../src/components/TrainingPanel";
import type { DatasetVersion, Job, ModelInfo, Project, TrainingRun } from "../src/types";

const project: Project = {
  id: "p-1",
  name: "Test",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "anpr-v1",
  workspace_path: "/workspace/p-1",
};

const version = (over: Partial<DatasetVersion> = {}): DatasetVersion =>
  ({
    id: "dv-1",
    project_id: "p-1",
    version: 1,
    created_at: "2026-09-19T00:00:00+00:00",
    split_seed: 7,
    config_snapshot_json: {},
    ...over,
  }) as DatasetVersion;

const model = (over: Partial<ModelInfo> = {}): ModelInfo => ({
  id: "yolo26n",
  label: "YOLO26 nano",
  kind: "builtin",
  weights_file: "yolo26n.pt",
  present: true,
  bytes: 1,
  note: "Fastest.",
  classes: [],
  ...over,
});

const run = (over: Partial<TrainingRun> = {}): TrainingRun => ({
  id: "tr-1",
  project_id: "p-1",
  dataset_version_id: "dv-1",
  base_model_id: "yolo26n",
  job_id: "job-1",
  epochs: 100,
  image_size: 640,
  settings_json: null,
  status: "completed",
  output_model_id: "yolo26n-v1",
  best_map50: 0.612,
  last_epoch: 100,
  error_message: null,
  started_at: "2026-09-19T00:00:00+00:00",
  completed_at: "2026-09-19T01:00:00+00:00",
  ...over,
});

const job = (over: Partial<Job> = {}): Job => ({
  id: "job-1",
  project_id: "p-1",
  type: "train",
  status: "running",
  progress: 0.3,
  progress_message: "Epoch 30 of 100 · loss 0.812 · mAP50 0.410",
  result_json: null,
  error_message: null,
  pid: 1,
  created_at: "2026-09-19T00:00:00+00:00",
  started_at: null,
  completed_at: null,
  ...over,
});

function panel(jobs: Job[] = []) {
  return render(<TrainingPanel project={project} jobs={jobs} />);
}

describe("TrainingPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listDatasetVersions").mockResolvedValue([version()]);
    vi.spyOn(api, "listTrainingRuns").mockResolvedValue([]);
    vi.spyOn(api, "listModels").mockResolvedValue([model()]);
    vi.spyOn(api, "startTraining").mockResolvedValue({ run: run({ status: "pending" }), job_id: "job-1" });
  });

  it("says what is missing when there is nothing to train on", async () => {
    vi.mocked(api.listDatasetVersions).mockResolvedValue([]);
    panel();

    expect(await screen.findByText(/export a dataset version first/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^train$/i })).not.toBeInTheDocument();
  });

  it("trains the chosen version with the chosen settings", async () => {
    panel();
    await screen.findByRole("button", { name: /^train$/i });
    fireEvent.change(screen.getByLabelText(/epochs/i), { target: { value: "25" } });

    fireEvent.click(screen.getByRole("button", { name: /^train$/i }));

    await waitFor(() =>
      expect(api.startTraining).toHaveBeenCalledWith(project.id, {
        datasetVersionId: "dv-1",
        baseModelId: "yolo26n",
        epochs: 25,
        imageSize: 640,
      }),
    );
  });

  it("can start from a model trained here before", async () => {
    // The loop the whole app exists to serve.
    vi.mocked(api.listModels).mockResolvedValue([
      model(),
      model({ id: "yolo26n-v1", label: "yolo26n-v1", kind: "custom", weights_file: "yolo26n-v1.pt" }),
    ]);
    panel();

    fireEvent.change(await screen.findByLabelText(/start from/i), { target: { value: "yolo26n-v1" } });
    fireEvent.click(screen.getByRole("button", { name: /^train$/i }));

    await waitFor(() =>
      expect(api.startTraining).toHaveBeenCalledWith(
        project.id,
        expect.objectContaining({ baseModelId: "yolo26n-v1" }),
      ),
    );
  });

  it("shows the epoch and the metric, not a spinner", async () => {
    vi.mocked(api.listTrainingRuns).mockResolvedValue([run({ status: "running", last_epoch: 30 })]);
    panel([job()]);

    // Twice: the live line reads from the run row, the detail under
    // it from the job's own message. Not three times - the run in
    // progress is kept out of the history list below.
    expect(await screen.findAllByText(/epoch 30 of 100/i)).toHaveLength(2);
    expect(screen.getByText(/mAP50 0\.410/)).toBeInTheDocument();
  });

  it("will not offer to start a second run while one is going", async () => {
    // This machine has one GPU. The backend refuses it too; not
    // offering the button is how the user finds out before clicking.
    vi.mocked(api.listTrainingRuns).mockResolvedValue([run({ status: "running" })]);
    panel([job()]);

    const button = await screen.findByRole("button", { name: /already going/i });
    expect(button).toBeDisabled();
  });

  it("passes on the reason when the backend refuses", async () => {
    vi.mocked(api.startTraining).mockRejectedValue(
      new ApiError(409, "training_busy", "A training run started 14:02 is still going."),
    );
    panel();

    fireEvent.click(await screen.findByRole("button", { name: /^train$/i }));

    expect(await screen.findByText(/still going/i)).toBeInTheDocument();
  });

  it("says which model a finished run produced, so it can be used", async () => {
    vi.mocked(api.listTrainingRuns).mockResolvedValue([run()]);
    panel();

    expect(await screen.findByText(/added as yolo26n-v1/i)).toBeInTheDocument();
    expect(screen.getByText(/mAP50 0\.612/)).toBeInTheDocument();
  });

  it("says why a failed run failed", async () => {
    vi.mocked(api.listTrainingRuns).mockResolvedValue([
      run({ status: "failed", output_model_id: null, error_message: "CUDA out of memory" }),
    ]);
    panel();

    expect(await screen.findByText(/cuda out of memory/i)).toBeInTheDocument();
  });

  it("catches up when a training job ends", async () => {
    // A run finishes minutes to hours after the click that started
    // it, so the list reacts to the job, not to the request.
    const { rerender } = render(<TrainingPanel project={project} jobs={[job()]} />);
    await screen.findByRole("button", { name: /^train$/i });
    vi.mocked(api.listTrainingRuns).mockClear();

    rerender(<TrainingPanel project={project} jobs={[job({ status: "succeeded" })]} />);

    await waitFor(() => expect(api.listTrainingRuns).toHaveBeenCalled());
  });

  it("says training survives the app closing", async () => {
    panel();

    expect(await screen.findByText(/keeps going if you close the app/i)).toBeInTheDocument();
  });
});

describe("TrainingPanel: when the history cannot be read", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listDatasetVersions").mockResolvedValue([version()]);
    vi.spyOn(api, "listModels").mockResolvedValue([model()]);
    vi.spyOn(api, "startTraining").mockResolvedValue({ run: run({ status: "pending" }), job_id: "job-1" });
    vi.spyOn(api, "listTrainingRuns").mockRejectedValue(new Error("no such table: training_runs"));
  });

  it("still lets a run be started", async () => {
    // The two failures mean different things, and one taking the
    // other down with it left a panel with nothing in it at all.
    render(<TrainingPanel project={project} jobs={[]} />);

    fireEvent.click(await screen.findByRole("button", { name: /^train$/i }));

    await waitFor(() => expect(api.startTraining).toHaveBeenCalled());
  });

  it("says nothing about it, because there may simply be none", async () => {
    render(<TrainingPanel project={project} jobs={[]} />);
    await screen.findByRole("button", { name: /^train$/i });

    expect(screen.queryByText(/no such table/i)).not.toBeInTheDocument();
  });
});

describe("TrainingPanel: when validation had to borrow a split", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listDatasetVersions").mockResolvedValue([version()]);
    vi.spyOn(api, "listModels").mockResolvedValue([model()]);
    vi.spyOn(api, "startTraining").mockResolvedValue({ run: run({ status: "pending" }), job_id: "job-1" });
  });

  it("says what the score was measured on when it was not a real val set", async () => {
    // An mAP measured on the training images is not a measure of
    // anything, and looks identical to one that is.
    vi.spyOn(api, "listTrainingRuns").mockResolvedValue([
      run({
        settings_json: {
          validated_on: "train",
          note: "The export has no validation or test images, so this run validated on the images it trained on.",
        },
      }),
    ]);
    render(<TrainingPanel project={project} jobs={[]} />);

    expect(await screen.findByText(/validated on the images it trained on/i)).toBeInTheDocument();
  });

  it("says nothing extra when the validation split was real", async () => {
    vi.spyOn(api, "listTrainingRuns").mockResolvedValue([run({ settings_json: { validated_on: "val" } })]);
    render(<TrainingPanel project={project} jobs={[]} />);

    await screen.findByText(/added as yolo26n-v1/i);
    expect(screen.queryByText(/validated on/i)).not.toBeInTheDocument();
  });
});
