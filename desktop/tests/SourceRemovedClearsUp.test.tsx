/**
 * Removing a source takes its tracks and frames with it, and the rest
 * of the app has to stop showing them.
 *
 * What went wrong: after removing both sources the track list still
 * held six rows. Clicking one answered "Track not found", because the
 * panel only refreshed jobs.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import SourcePanel from "../src/components/SourcePanel";
import type { Project, Source, SourceContents } from "../src/types";

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

const contents: SourceContents = {
  frames: 12,
  tracks: 6,
  labels: 3,
  bytes: 1024,
  running_jobs: 0,
  files_removed: false,
};

describe("Removing a source tells the rest of the app", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listSources").mockResolvedValue([source]);
    vi.spyOn(api, "listModels").mockResolvedValue([]);
    vi.spyOn(api, "getSourceContents").mockResolvedValue(contents);
    vi.spyOn(api, "deleteSource").mockResolvedValue({ ...contents, files_removed: true });
  });

  async function removeIt(onRemoved = vi.fn()) {
    render(
      <SourcePanel
        project={project}
        onProcessed={vi.fn()}
        onWatch={vi.fn()}
        onRemoved={onRemoved}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /remove 160\.187/i }));
    fireEvent.click(await screen.findByRole("button", { name: /remove this source/i }));
    return onRemoved;
  }

  it("says which source went, so what is derived from it can go too", async () => {
    const onRemoved = await removeIt();

    await waitFor(() => expect(onRemoved).toHaveBeenCalledWith("s-1"));
  });

  it("says nothing when the removal failed", async () => {
    // Telling the app to drop a source's tracks when the source is
    // still there would empty a list for no reason.
    vi.mocked(api.deleteSource).mockRejectedValue(new Error("busy"));
    const onRemoved = await removeIt();

    await waitFor(() => expect(screen.getByText(/busy/i)).toBeInTheDocument());
    expect(onRemoved).not.toHaveBeenCalled();
  });
});
