/**
 * Choosing which model detects.
 *
 * The detector was one hard-coded file, so there was no way to try a
 * bigger model on a hard clip and no way at all to use one you trained.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import RtspPanel from "../src/components/RtspPanel";
import SourcePanel from "../src/components/SourcePanel";
import type { Job, JobSubmitted, ModelInfo, Project, RtspStartResult, Source } from "../src/types";

const project: Project = {
  id: "p-1",
  name: "Test",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "anpr-v1",
  workspace_path: "/workspace/p-1",
};

const source: Source = {
  id: "s-1",
  project_id: "p-1",
  type: "video",
  path_or_uri: "C:/clips/atcc1.mp4",
  fps: 25,
  width: 1920,
  height: 1080,
  frame_count: 9000,
  duration_ms: 360000,
  created_at: "2026-09-19T00:00:00+00:00",
  is_processing: false,
  is_frozen: false,
  ground_truth_vehicle_count: null,
};

const job: Job = {
  id: "job-1",
  project_id: "p-1",
  type: "detect",
  status: "pending",
  progress: 0,
  progress_message: null,
  result_json: null,
  error_message: null,
  pid: 1,
  created_at: "2026-09-19T00:00:00+00:00",
  started_at: null,
  completed_at: null,
};

const submitted: JobSubmitted = { job, run_id: "run-1" };

const model = (over: Partial<ModelInfo> = {}): ModelInfo => ({
  id: "yolo26n",
  label: "YOLO26 nano",
  kind: "builtin",
  weights_file: "yolo26n.pt",
  present: true,
  bytes: 5_544_453,
  note: "Fastest.",
  ...over,
});

const both = [
  model(),
  model({ id: "yolo26s", label: "YOLO26 small", weights_file: "yolo26s.pt", present: false, bytes: 0 }),
];

describe("Choosing the detection model", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listSources").mockResolvedValue([source]);
    vi.spyOn(api, "listModels").mockResolvedValue(both);
    vi.spyOn(api, "processSource").mockResolvedValue(submitted);
  });

  it("offers both built-in models next to Detect + Track", async () => {
    render(<SourcePanel project={project} onProcessed={vi.fn()} onWatch={vi.fn()} />);

    const picker = await screen.findByLabelText(/^model$/i);
    expect(picker).toHaveTextContent(/YOLO26 nano/);
    expect(picker).toHaveTextContent(/YOLO26 small/);
  });

  it("detects with the model that was chosen", async () => {
    render(<SourcePanel project={project} onProcessed={vi.fn()} onWatch={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText(/^model$/i), { target: { value: "yolo26s" } });

    fireEvent.click(screen.getByRole("button", { name: /detect \+ track/i }));

    await waitFor(() =>
      expect(api.processSource).toHaveBeenCalledWith(project.id, source.id, expect.any(Number), "yolo26s", false),
    );
  });

  it("says when a model has to be downloaded before it can run", async () => {
    // Said before the run, not discovered during it.
    render(<SourcePanel project={project} onProcessed={vi.fn()} onWatch={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText(/^model$/i), { target: { value: "yolo26s" } });

    expect(await screen.findByText(/not downloaded yet/i)).toBeInTheDocument();
  });

  it("remembers the choice for next time, per project", async () => {
    const first = render(<SourcePanel project={project} onProcessed={vi.fn()} onWatch={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText(/^model$/i), { target: { value: "yolo26s" } });
    first.unmount();

    render(<SourcePanel project={project} onProcessed={vi.fn()} onWatch={vi.fn()} />);

    await waitFor(() => expect(screen.getByLabelText(/^model$/i)).toHaveValue("yolo26s"));
  });

  it("falls back to a model that exists when the remembered one is gone", async () => {
    // A 404 on Detect is a confusing way to learn a file was deleted.
    window.localStorage.setItem(`anpr:last-model:${project.id}`, "gantry-v3");

    render(<SourcePanel project={project} onProcessed={vi.fn()} onWatch={vi.fn()} />);

    await waitFor(() => expect(screen.getByLabelText(/^model$/i)).toHaveValue("yolo26n"));
  });

  it("offers a model you trained yourself", async () => {
    vi.mocked(api.listModels).mockResolvedValue([
      ...both,
      model({ id: "gantry-v3", label: "gantry-v3", kind: "custom", weights_file: "gantry-v3.pt", note: "Your own model." }),
    ]);

    render(<SourcePanel project={project} onProcessed={vi.fn()} onWatch={vi.fn()} />);

    expect(await screen.findByLabelText(/^model$/i)).toHaveTextContent(/gantry-v3 \(yours\)/);
  });
});

describe("Choosing the model for a live stream", () => {
  const started: RtspStartResult = {
    source: { ...source, id: "s-live", type: "rtsp", path_or_uri: "rtsp://camera/ch1" },
    run: {
      id: "run-live",
      source_id: "s-live",
      detector_version: "yolo26s",
      tracker_config: null,
      sampling_config: {},
      status: "running",
      sampled_frame_count: null,
      error_message: null,
      started_at: "2026-09-19T00:00:00+00:00",
      completed_at: null,
    },
  };

  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listModels").mockResolvedValue(both);
    vi.spyOn(api, "startRtspSession").mockResolvedValue(started);
    vi.spyOn(api, "getRtspStatus").mockResolvedValue({
      run_id: "run-live",
      connected: true,
      reconnect_attempts: 0,
      frames_captured: 0,
      frames_dropped: 0,
      tracks_persisted: 0,
      stopped: false,
      error: null,
    });
  });

  it("starts the stream with the chosen model", async () => {
    // A live session is where a model you trained earns its keep.
    render(<RtspPanel project={project} onSessionEnded={vi.fn()} onShowPreview={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText(/^model$/i), { target: { value: "yolo26s" } });
    fireEvent.change(screen.getByPlaceholderText(/rtsp:\/\//i), { target: { value: "rtsp://camera/ch1" } });

    fireEvent.click(screen.getByRole("button", { name: /start live capture/i }));

    await waitFor(() =>
      expect(api.startRtspSession).toHaveBeenCalledWith(
        project.id,
        "rtsp://camera/ch1",
        expect.any(Number),
        "yolo26s",
      ),
    );
  });
});
