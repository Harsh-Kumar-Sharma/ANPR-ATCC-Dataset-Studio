/**
 * Keeping a live session's frames, and clearing out the rest.
 *
 * A real session captured 2,453 frames and persisted no tracks, which
 * left nothing at all to label. Keeping the frames is what makes such
 * a session useful; sweeping them afterwards is what keeps it
 * affordable.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import LabelQueue from "../src/components/LabelQueue";
import RtspPanel from "../src/components/RtspPanel";
import { useFrameQueue } from "../src/useFrameQueue";
import type { Frame, Project, QueueProgress, RtspStartResult, Sweep } from "../src/types";

const project: Project = {
  id: "p-1",
  name: "Test",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "anpr-v1",
  workspace_path: "/workspace/p-1",
};

const started: RtspStartResult = {
  source: {
    id: "s-live",
    project_id: "p-1",
    type: "rtsp",
    path_or_uri: "rtsp://camera/ch1",
    fps: 10,
    width: 0,
    height: 0,
    frame_count: 0,
    duration_ms: 0,
    created_at: "2026-09-19T00:00:00+00:00",
    is_processing: false,
    is_frozen: false,
    ground_truth_vehicle_count: null,
  },
  run: {
    id: "run-live",
    source_id: "s-live",
    detector_version: "yolo26n",
    tracker_config: null,
    sampling_config: {},
    status: "running",
    sampled_frame_count: null,
    error_message: null,
    started_at: "2026-09-19T00:00:00+00:00",
    completed_at: null,
  },
};

const liveStatus = (over = {}) => ({
  run_id: "run-live",
  connected: true,
  reconnect_attempts: 0,
  frames_captured: 2453,
  frames_dropped: 0,
  tracks_persisted: 0,
  frames_saved: 0,
  stopped: false,
  error: null,
  ...over,
});

describe("Keeping the frames a live session captures", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listModels").mockResolvedValue([]);
    vi.spyOn(api, "startRtspSession").mockResolvedValue(started);
    vi.spyOn(api, "getRtspStatus").mockResolvedValue(liveStatus());
  });

  function panel() {
    return render(<RtspPanel project={project} onSessionEnded={vi.fn()} onShowPreview={vi.fn()} />);
  }

  async function start() {
    fireEvent.change(await screen.findByPlaceholderText(/rtsp:\/\//i), {
      target: { value: "rtsp://camera/ch1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /start live capture/i }));
  }

  it("keeps nothing unless it is asked to", async () => {
    // Every frame of a long session at 1080p is gigabytes.
    panel();
    await start();

    await waitFor(() =>
      expect(api.startRtspSession).toHaveBeenCalledWith(
        project.id,
        "rtsp://camera/ch1",
        expect.any(Number),
        null,
        { keepFrames: false, every: 10 },
      ),
    );
  });

  it("keeps one frame in ten when asked", async () => {
    panel();
    fireEvent.click(await screen.findByLabelText(/save captured frames/i));
    await start();

    await waitFor(() =>
      expect(api.startRtspSession).toHaveBeenCalledWith(
        project.id,
        "rtsp://camera/ch1",
        expect.any(Number),
        null,
        { keepFrames: true, every: 10 },
      ),
    );
  });

  it("takes a different interval", async () => {
    panel();
    fireEvent.click(await screen.findByLabelText(/save captured frames/i));
    fireEvent.change(screen.getByLabelText(/keep one frame in/i), { target: { value: "25" } });
    await start();

    await waitFor(() =>
      expect(api.startRtspSession).toHaveBeenCalledWith(
        project.id,
        "rtsp://camera/ch1",
        expect.any(Number),
        null,
        { keepFrames: true, every: 25 },
      ),
    );
  });

  it("says what turning it off means, so the choice is a choice", async () => {
    panel();

    expect(await screen.findByText(/only vehicles the model detects/i)).toBeInTheDocument();
  });

  it("counts the frames it kept, and only once there are some", async () => {
    // "Frames saved 0" would read as a failure to someone who never
    // asked for any.
    vi.mocked(api.getRtspStatus).mockResolvedValue(liveStatus({ frames_saved: 0 }));
    panel();
    await start();
    await screen.findByText(/frames captured/i);
    expect(screen.queryByText(/frames saved to label/i)).not.toBeInTheDocument();

    vi.mocked(api.getRtspStatus).mockResolvedValue(liveStatus({ frames_saved: 245 }));

    expect(await screen.findByText(/frames saved to label/i, {}, { timeout: 3000 })).toBeInTheDocument();
  });
});

// --- and clearing out what nobody labelled -----------------------------------

function frame(index: number, status: Frame["status"] = "pending"): Frame {
  return {
    id: `f-${index}`,
    source_id: "s-1",
    frame_index: index,
    timestamp_ms: index * 100,
    width: 640,
    height: 480,
    status,
    selection_reason: null,
  };
}

const sweep = (over: Partial<Sweep> = {}): Sweep => ({
  kept: 2,
  deleted: 243,
  held: 0,
  bytes_freed: 51_000_000,
  ...over,
});

function Queue() {
  const [selected, setSelected] = useState<string | null>(null);
  const queue = useFrameQueue({ project, selectedFrameId: selected, onSelect: (f) => setSelected(f.id) });
  return <LabelQueue queue={queue} selectedFrameId={selected} project={project} />;
}

describe("Deleting the frames nobody labelled", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listFrames").mockResolvedValue([frame(0), frame(1, "labeled")]);
    vi.spyOn(api, "getQueueProgress").mockResolvedValue({
      pending: 1,
      labeled: 1,
      rejected: 0,
      skipped: 0,
      total: 2,
    } as QueueProgress);
    vi.spyOn(api, "getQueueBySource").mockResolvedValue([]);
    vi.spyOn(api, "previewSweep").mockResolvedValue(sweep());
    vi.spyOn(api, "sweepUnlabelled").mockResolvedValue(sweep());
  });

  it("says what it would take before taking it", async () => {
    render(<Queue />);

    fireEvent.click(await screen.findByRole("button", { name: /delete the frames i did not label/i }));

    const confirm = await screen.findByRole("alert");
    expect(confirm).toHaveTextContent(/243/);
    expect(confirm).toHaveTextContent(/49 MB/);
    expect(confirm).toHaveTextContent(/2.*labelled frame\(s\) stay/i);
    expect(api.sweepUnlabelled).not.toHaveBeenCalled();
  });

  it("deletes only when confirmed", async () => {
    render(<Queue />);
    fireEvent.click(await screen.findByRole("button", { name: /delete the frames i did not label/i }));

    fireEvent.click(await screen.findByRole("button", { name: /cancel/i }));
    expect(api.sweepUnlabelled).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /delete the frames i did not label/i }));
    fireEvent.click(await screen.findByRole("button", { name: /^delete them$/i }));

    await waitFor(() => expect(api.sweepUnlabelled).toHaveBeenCalledWith(project.id, null));
  });

  it("offers nothing to press when there is nothing to delete", async () => {
    vi.mocked(api.previewSweep).mockResolvedValue(sweep({ deleted: 0, bytes_freed: 0 }));
    render(<Queue />);

    fireEvent.click(await screen.findByRole("button", { name: /delete the frames i did not label/i }));

    expect(await screen.findByText(/every frame here has been labelled/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^delete them$/i })).not.toBeInTheDocument();
  });

  it("says why a frame stayed behind when one did", async () => {
    // A frame that survives a delete the user asked for needs a reason.
    vi.mocked(api.sweepUnlabelled).mockResolvedValue(sweep({ deleted: 240, held: 3 }));
    render(<Queue />);
    fireEvent.click(await screen.findByRole("button", { name: /delete the frames i did not label/i }));
    fireEvent.click(await screen.findByRole("button", { name: /^delete them$/i }));

    expect(await screen.findByText(/3 stayed because a dataset version/i)).toBeInTheDocument();
  });

  it("reloads the queue once frames have gone", async () => {
    render(<Queue />);
    await screen.findByRole("button", { name: /delete the frames i did not label/i });
    vi.mocked(api.listFrames).mockClear();

    fireEvent.click(screen.getByRole("button", { name: /delete the frames i did not label/i }));
    fireEvent.click(await screen.findByRole("button", { name: /^delete them$/i }));

    await waitFor(() => expect(api.listFrames).toHaveBeenCalled());
  });
});
