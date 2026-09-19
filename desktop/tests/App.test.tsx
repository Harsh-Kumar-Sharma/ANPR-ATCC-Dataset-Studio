import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import App from "../src/App";

describe("App", () => {
  it("renders the studio title", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: /ANPR \+ ATCC Dataset Studio/i })).toBeInTheDocument();
  });
});

describe("App: a source that has gone", () => {
  const project = {
    id: "p-1",
    name: "ANPR",
    created_at: "2026-09-19T00:00:00+00:00",
    class_schema_version: "anpr-v1",
    workspace_path: "/workspace/p-1",
  };

  const source = {
    id: "s-1",
    project_id: "p-1",
    type: "rtsp",
    path_or_uri: "rtsp://admin:secret@160.187.179.196:9001/Streaming/channels/1",
    fps: 10,
    width: 0,
    height: 0,
    frame_count: 0,
    duration_ms: 0,
    created_at: "2026-09-19T00:00:00+00:00",
    is_processing: false,
    is_frozen: false,
    ground_truth_vehicle_count: null,
  };

  const track = {
    id: "t-1",
    run_id: "run-1",
    tracker_track_id: 1,
    start_ts: 19415,
    end_ts: 19415,
    bucket: "HARD",
    review_status: "accepted",
    reviewed_frame_candidate_id: null,
    reviewed_class_id: null,
    reviewed_bbox_json: null,
  };

  const contents = { frames: 12, tracks: 1, labels: 3, bytes: 1024, running_jobs: 0, files_removed: false };

  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listProjects").mockResolvedValue([project as never]);
    vi.spyOn(api, "listSources").mockResolvedValue([source as never]);
    vi.spyOn(api, "listModels").mockResolvedValue([]);
    vi.spyOn(api, "listJobs").mockResolvedValue([]);
    vi.spyOn(api, "listTracks").mockResolvedValue([track as never]);
    vi.spyOn(api, "getSourceContents").mockResolvedValue(contents as never);
    vi.spyOn(api, "deleteSource").mockResolvedValue({ ...contents, files_removed: true } as never);
  });

  it("stops listing the tracks that went with the source", async () => {
    // What went wrong: after removing both sources the list still
    // held six tracks, and clicking one answered "Track not found".
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /open/i }));
    await screen.findByText(/19415/);

    // The source goes, and the backend has no tracks left to report.
    vi.mocked(api.listSources).mockResolvedValue([]);
    vi.mocked(api.listTracks).mockResolvedValue([]);
    fireEvent.click(await screen.findByRole("button", { name: /remove 160\.187/i }));
    fireEvent.click(await screen.findByRole("button", { name: /remove this source/i }));

    await waitFor(() => expect(screen.queryByText(/19415/)).not.toBeInTheDocument());
  });
});
