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
import type { Frame, LiveCamera, Project, QueueProgress, RtspStartResult, Sweep } from "../src/types";

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
    stored_frames: 0,
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
        { keepFrames: false, every: 10, perVehicle: 3 },
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
        { keepFrames: true, every: 10, perVehicle: 3 },
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
        { keepFrames: true, every: 25, perVehicle: 3 },
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

  it("sits above the list, not below it", async () => {
    // Below a hundred and forty rows it may as well not exist, which
    // is how someone who had finished labelling came to ask for a
    // feature that was already there.
    render(<Queue />);
    const button = await screen.findByRole("button", { name: /delete the frames i did not label/i });
    const firstFrame = screen.getByRole("button", { name: /^frame 0$/i });

    const order = button.compareDocumentPosition(firstFrame);

    expect(order & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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

// --- and not retyping the camera every time ---------------------------------

const camera = (over: Partial<LiveCamera> = {}): LiveCamera => ({
  id: "cam-1",
  project_id: "p-1",
  rtsp_url: "rtsp://admin:secret@10.0.0.4:9001/Streaming/channels/1",
  expected_fps: 25,
  model_id: null,
  keep_frames: true,
  keep_every: 15,
  last_used_at: "2026-09-19T00:00:00+00:00",
  ...over,
});

describe("Starting a camera you have started before", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listModels").mockResolvedValue([]);
    vi.spyOn(api, "startRtspSession").mockResolvedValue(started);
    vi.spyOn(api, "getRtspStatus").mockResolvedValue(liveStatus());
    vi.spyOn(api, "listLiveCameras").mockResolvedValue([camera()]);
  });

  function panel() {
    return render(<RtspPanel project={project} onSessionEnded={vi.fn()} onShowPreview={vi.fn()} />);
  }

  it("fills the whole configuration back in, not just the url", async () => {
    // Stop is the most likely moment to want the same camera back.
    panel();

    await waitFor(() => expect(screen.getByPlaceholderText(/rtsp:\/\//i)).toHaveValue(camera().rtsp_url));
    expect(screen.getByLabelText(/camera fps/i)).toHaveValue(25);
    expect(screen.getByLabelText(/save captured frames/i)).toBeChecked();
    expect(screen.getByLabelText(/keep one frame in/i)).toHaveValue(15);
  });

  it("starts again with one click, and no retyping", async () => {
    panel();
    await waitFor(() => expect(screen.getByPlaceholderText(/rtsp:\/\//i)).toHaveValue(camera().rtsp_url));

    fireEvent.click(screen.getByRole("button", { name: /start live capture/i }));

    await waitFor(() =>
      expect(api.startRtspSession).toHaveBeenCalledWith(project.id, camera().rtsp_url, 25, null, {
        keepFrames: true,
        every: 15,
        perVehicle: 3,
      }),
    );
  });

  it("does not overwrite a url someone is halfway through typing", async () => {
    // Worse than not remembering at all.
    let release: (cameras: LiveCamera[]) => void = () => {};
    vi.mocked(api.listLiveCameras).mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    panel();
    fireEvent.change(await screen.findByPlaceholderText(/rtsp:\/\//i), {
      target: { value: "rtsp://a-different-camera/1" },
    });

    release([camera()]);

    await waitFor(() => expect(api.listLiveCameras).toHaveBeenCalled());
    expect(screen.getByPlaceholderText(/rtsp:\/\//i)).toHaveValue("rtsp://a-different-camera/1");
  });

  it("offers no menu for a single camera, which is not a choice", async () => {
    panel();
    await waitFor(() => expect(screen.getByPlaceholderText(/rtsp:\/\//i)).toHaveValue(camera().rtsp_url));

    expect(screen.queryByLabelText(/saved camera/i)).not.toBeInTheDocument();
  });

  it("offers a menu once there are two, named so they can be told apart", async () => {
    vi.mocked(api.listLiveCameras).mockResolvedValue([
      camera(),
      camera({ id: "cam-2", rtsp_url: "rtsp://admin:secret@10.0.0.5:9001/Streaming/channels/2" }),
    ]);
    panel();

    const picker = await screen.findByLabelText(/saved camera/i);
    expect(picker).toHaveTextContent(/10\.0\.0\.4 · ch 1/);
    expect(picker).toHaveTextContent(/10\.0\.0\.5 · ch 2/);
    expect(picker).not.toHaveTextContent(/secret/);
  });

  it("switches the whole configuration when another camera is picked", async () => {
    vi.mocked(api.listLiveCameras).mockResolvedValue([
      camera(),
      camera({
        id: "cam-2",
        rtsp_url: "rtsp://admin:secret@10.0.0.5:9001/Streaming/channels/2",
        expected_fps: 5,
        keep_frames: false,
        keep_every: 30,
      }),
    ]);
    panel();

    fireEvent.change(await screen.findByLabelText(/saved camera/i), { target: { value: "cam-2" } });

    expect(screen.getByLabelText(/camera fps/i)).toHaveValue(5);
    expect(screen.getByLabelText(/save captured frames/i)).not.toBeChecked();
  });

  it("can forget a camera, and says the footage stays", async () => {
    const forget = vi.spyOn(api, "forgetLiveCamera").mockResolvedValue(undefined);
    panel();

    fireEvent.click(await screen.findByRole("button", { name: /forget this camera/i }));

    await waitFor(() => expect(forget).toHaveBeenCalledWith(project.id, "cam-1"));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /forget this camera/i })).not.toBeInTheDocument(),
    );
  });

  it("offers nothing to forget when nothing is remembered", async () => {
    vi.mocked(api.listLiveCameras).mockResolvedValue([]);
    panel();
    await screen.findByPlaceholderText(/rtsp:\/\//i);

    expect(screen.queryByRole("button", { name: /forget this camera/i })).not.toBeInTheDocument();
  });
});

describe("frames per vehicle", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listModels").mockResolvedValue([]);
    vi.spyOn(api, "listLiveCameras").mockResolvedValue([]);
  });

  it("sends how many frames of one vehicle to keep", async () => {
    const start = vi.spyOn(api, "startRtspSession").mockResolvedValue({
      source: {} as never,
      run: { id: "run-1" } as never,
    });
    render(<RtspPanel project={project} onSessionEnded={() => {}} onShowPreview={() => {}} />);

    fireEvent.change(screen.getByPlaceholderText(/rtsp:\/\//i), { target: { value: "rtsp://camera/ch1" } });
    fireEvent.change(screen.getByLabelText(/frames per vehicle/i), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: /start live capture/i }));

    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(
        project.id,
        "rtsp://camera/ch1",
        expect.any(Number),
        null,
        expect.objectContaining({ perVehicle: 2 }),
      ),
    );
  });
});
