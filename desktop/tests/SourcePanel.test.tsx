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
  stored_frames: 0,
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

describe("SourcePanel: getting to a source's frames", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listSources").mockResolvedValue([source]);
  });

  it("opens this source's frames when its name is clicked", async () => {
    // Not knowing which frames came from which clip is the complaint
    // this answers, and the name is the obvious thing to click.
    const onLabel = vi.fn();
    render(<SourcePanel project={project} onProcessed={vi.fn()} onWatch={vi.fn()} onLabel={onLabel} />);

    fireEvent.click(await screen.findByRole("button", { name: /^atcc1\.mp4$/i }));

    expect(onLabel).toHaveBeenCalledWith(expect.objectContaining({ id: "s-1" }));
  });
});

describe("SourcePanel: how many frames a source holds", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listModels").mockResolvedValue([]);
  });

  it("says what a live source actually holds, not its sentinel zero", async () => {
    // A live stream has no known length, so frame_count is 0. Saying
    // "0 frames" for a source holding three hundred is how empty
    // sources became impossible to tell from full ones.
    vi.spyOn(api, "listSources").mockResolvedValue([
      { ...source, type: "rtsp", path_or_uri: "rtsp://10.0.0.4/ch1", frame_count: 0, stored_frames: 303 },
    ]);
    render(<SourcePanel project={project} onProcessed={vi.fn()} onWatch={vi.fn()} />);

    expect(await screen.findByText(/303 frame\(s\) saved/i)).toBeInTheDocument();
    expect(screen.queryByText(/· 0 frames/)).not.toBeInTheDocument();
  });

  it("still describes a video by its own numbers", async () => {
    vi.spyOn(api, "listSources").mockResolvedValue([{ ...source, frame_count: 9000, stored_frames: 12 }]);
    render(<SourcePanel project={project} onProcessed={vi.fn()} onWatch={vi.fn()} />);

    expect(await screen.findByText(/9000 frames/)).toBeInTheDocument();
  });
});
