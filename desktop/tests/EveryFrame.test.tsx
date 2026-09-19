/**
 * Running over every frame, and knowing what it costs first.
 *
 * The disk is the reason this is a choice rather than the default: a
 * 90,003-frame clip against 9.4 GB free.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import SourcePanel from "../src/components/SourcePanel";
import type { Job, JobSubmitted, ModelInfo, Project, RunEstimate, Source } from "../src/types";

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
  path_or_uri: "C:/clips/day_anpr_gantry.mp4",
  fps: 30,
  width: 1920,
  height: 1080,
  frame_count: 90003,
  duration_ms: 3000100,
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

const model: ModelInfo = {
  id: "yolo26n",
  label: "YOLO26 nano",
  kind: "builtin",
  weights_file: "yolo26n.pt",
  present: true,
  bytes: 5_544_453,
  note: "Fastest.",
};

const estimate = (over: Partial<RunEstimate> = {}): RunEstimate => ({
  frames_to_process: 90003,
  rows_expected: 180006,
  bytes_now: 72_002_400,
  bytes_if_every_frame_reviewed: 22_400_000_000,
  free_bytes: 9_400_000_000,
  fits: true,
  reason: null,
  ...over,
});

function renderPanel() {
  return render(<SourcePanel project={project} onProcessed={vi.fn()} onWatch={vi.fn()} />);
}

describe("Keeping every frame with a vehicle in it", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listSources").mockResolvedValue([source]);
    vi.spyOn(api, "listModels").mockResolvedValue([model]);
    vi.spyOn(api, "processSource").mockResolvedValue({ job, run_id: "run-1" } as JobSubmitted);
    vi.spyOn(api, "estimateRun").mockResolvedValue(estimate());
  });

  it("samples by default, because that is the cheap answer", async () => {
    renderPanel();
    await screen.findByRole("button", { name: /detect \+ track/i });

    fireEvent.click(screen.getByRole("button", { name: /detect \+ track/i }));

    await waitFor(() =>
      expect(api.processSource).toHaveBeenCalledWith(project.id, source.id, expect.any(Number), "yolo26n", false),
    );
  });

  it("runs over the whole clip when asked to", async () => {
    renderPanel();
    fireEvent.click(await screen.findByLabelText(/every frame with a vehicle/i));

    fireEvent.click(screen.getByRole("button", { name: /detect \+ track/i }));

    await waitFor(() =>
      expect(api.processSource).toHaveBeenCalledWith(project.id, source.id, expect.any(Number), "yolo26n", true),
    );
  });

  it("says what the mode actually does", async () => {
    renderPanel();

    fireEvent.click(await screen.findByLabelText(/every frame with a vehicle/i));

    expect(screen.getByText(/walks the whole clip/i)).toBeInTheDocument();
  });

  it("shows the cost before the button is pressed", async () => {
    // Not at 80% with the disk full and a half-written run to clean up.
    renderPanel();

    fireEvent.mouseEnter(await screen.findByRole("button", { name: /detect \+ track/i }));

    const shown = await screen.findByTestId("run-estimate");
    expect(shown).toHaveTextContent(/90,003 frames/);
    expect(shown).toHaveTextContent(/if you label all of it/i);
  });

  it("separates what the run writes from what reviewing it would cost", async () => {
    // The second number is the one that fills a disk, and hiding it
    // would make the estimate useless.
    renderPanel();

    fireEvent.mouseEnter(await screen.findByRole("button", { name: /detect \+ track/i }));

    const shown = await screen.findByTestId("run-estimate");
    expect(shown).toHaveTextContent(/69 MB now/);
    expect(shown).toHaveTextContent(/up to 21 GB/);
  });

  it("says plainly when a run will not fit", async () => {
    vi.mocked(api.estimateRun).mockResolvedValue(
      estimate({ fits: false, free_bytes: 1024, reason: "This run would write about 69 MB and only 1.0 KB is free." }),
    );
    renderPanel();

    fireEvent.mouseEnter(await screen.findByRole("button", { name: /detect \+ track/i }));

    const shown = await screen.findByTestId("run-estimate");
    expect(shown).toHaveTextContent(/only 1\.0 KB is free/);
    expect(shown).toHaveClass("run-estimate--no");
  });

  it("asks again when the mode changes, rather than showing a stale number", async () => {
    renderPanel();
    fireEvent.mouseEnter(await screen.findByRole("button", { name: /detect \+ track/i }));
    await screen.findByTestId("run-estimate");

    fireEvent.click(screen.getByLabelText(/every frame with a vehicle/i));

    expect(screen.queryByTestId("run-estimate")).not.toBeInTheDocument();
  });

  it("stays quiet when the estimate cannot be had", async () => {
    // An estimate that fails is not worth an error message: the run
    // itself still reports honestly.
    vi.mocked(api.estimateRun).mockRejectedValue(new Error("offline"));
    renderPanel();

    fireEvent.mouseEnter(await screen.findByRole("button", { name: /detect \+ track/i }));

    await waitFor(() => expect(api.estimateRun).toHaveBeenCalled());
    expect(screen.queryByTestId("run-estimate")).not.toBeInTheDocument();
  });
});
