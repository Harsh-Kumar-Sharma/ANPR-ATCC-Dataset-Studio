import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api";
import SourcePanel from "../src/components/SourcePanel";
import type { Job, JobSubmitted, Project, Source } from "../src/types";

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
  type: "select",
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

const submitted: JobSubmitted = { job, run_id: null };

function renderPanel() {
  return render(<SourcePanel project={project} onProcessed={vi.fn()} onWatch={vi.fn()} />);
}

describe("SourcePanel: choosing frames", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listSources").mockResolvedValue([source]);
  });

  it("can ask for the frames worth labelling", async () => {
    // Without this the selection service exists but nothing a user can
    // press ever runs it.
    const selectFrames = vi.spyOn(api, "selectFrames").mockResolvedValue(submitted);
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: /choose frames/i }));

    await waitFor(() => expect(selectFrames).toHaveBeenCalledWith("p-1", "s-1"));
  });

  it("says the work has been queued rather than pretending it is done", async () => {
    vi.spyOn(api, "selectFrames").mockResolvedValue(submitted);
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: /choose frames/i }));

    expect(await screen.findByText(/queued/i)).toBeInTheDocument();
  });

  it("shows the server's reason when selection cannot be started", async () => {
    vi.spyOn(api, "selectFrames").mockRejectedValue(
      new ApiError(409, "processing_already_running", "A processing run is already in progress for this source."),
    );
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: /choose frames/i }));

    expect(await screen.findByText(/already in progress/i)).toBeInTheDocument();
  });

  it("does not offer it for a live stream, which has no frames to choose from", async () => {
    vi.spyOn(api, "listSources").mockResolvedValue([{ ...source, type: "rtsp" }]);
    renderPanel();

    await screen.findByText(/atcc1\.mp4/);
    expect(screen.queryByRole("button", { name: /choose frames/i })).not.toBeInTheDocument();
  });
});
