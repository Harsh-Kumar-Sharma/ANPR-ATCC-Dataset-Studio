import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api";
import SourcePanel from "../src/components/SourcePanel";
import type { Project, Source, SourceContents } from "../src/types";

const project: Project = {
  id: "p-1",
  name: "ANPR",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "atcc-v1",
  workspace_path: "/workspace/p-1",
};

const video: Source = {
  id: "s-1",
  project_id: "p-1",
  type: "video",
  path_or_uri: "C:/clips/day_anpr_gantry.mp4",
  fps: 25,
  width: 1920,
  height: 1080,
  frame_count: 90003,
  duration_ms: 3600000,
  created_at: "2026-09-19T00:00:00+00:00",
  is_processing: false,
  is_frozen: false,
  ground_truth_vehicle_count: null,
};

const stream: Source = {
  ...video,
  id: "s-2",
  type: "rtsp",
  path_or_uri: "rtsp://admin:Admin1234@160.187.179.196:9001/Streaming/channels/1",
  width: 0,
  height: 0,
  frame_count: 0,
};

function contents(overrides: Partial<SourceContents> = {}): SourceContents {
  return {
    frames: 4029,
    tracks: 328,
    labels: 28,
    bytes: 1_200_000_000,
    running_jobs: 0,
    files_removed: false,
    ...overrides,
  };
}

function renderPanel(sources: Source[] = [video, stream]) {
  vi.spyOn(api, "listSources").mockResolvedValue(sources);
  return render(<SourcePanel project={project} onProcessed={vi.fn()} onWatch={vi.fn()} />);
}

function openDeleteFor(label: string) {
  fireEvent.click(screen.getByRole("button", { name: new RegExp(`remove ${label}`, "i") }));
}

describe("SourcePanel: removing a source", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getSourceContents").mockResolvedValue(contents());
  });

  it("offers a remove control per source", async () => {
    renderPanel();

    await screen.findByText(/day_anpr_gantry\.mp4/);
    expect(screen.getAllByTestId("remove-source")).toHaveLength(2);
  });

  it("says what removing would destroy before asking", async () => {
    renderPanel();
    await screen.findByText(/day_anpr_gantry\.mp4/);

    openDeleteFor("day_anpr_gantry.mp4");

    const summary = await screen.findByTestId("remove-source-summary");
    expect(summary.textContent).toContain("28");
    expect(summary.textContent).toContain("4029");
    expect(summary.textContent).toMatch(/1\.1 GB|1\.2 GB/);
  });

  it("removes the source and drops it from the list", async () => {
    const remove = vi.spyOn(api, "deleteSource").mockResolvedValue(contents({ files_removed: true }));
    renderPanel();
    await screen.findByText(/day_anpr_gantry\.mp4/);
    openDeleteFor("day_anpr_gantry.mp4");

    fireEvent.click(await screen.findByRole("button", { name: /remove this source/i }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith("p-1", "s-1"));
    // The row goes. Checked by its own control rather than by text,
    // because the message confirming the removal names the source too.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /remove day_anpr_gantry\.mp4/i })).not.toBeInTheDocument(),
    );
    expect(screen.getAllByTestId("remove-source")).toHaveLength(1);
  });

  it("refuses while work is running against it", async () => {
    vi.spyOn(api, "getSourceContents").mockResolvedValue(contents({ running_jobs: 1 }));
    renderPanel();
    await screen.findByText(/day_anpr_gantry\.mp4/);

    openDeleteFor("day_anpr_gantry.mp4");

    expect(await screen.findByText(/1 unfinished job\(s\) or live capture\(s\)/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /remove this source/i })).toBeDisabled();
  });

  it("shows the server's reason when it refuses", async () => {
    vi.spyOn(api, "deleteSource").mockRejectedValue(
      new ApiError(409, "source_busy", "1 job(s) are still running against this source."),
    );
    renderPanel();
    await screen.findByText(/day_anpr_gantry\.mp4/);
    openDeleteFor("day_anpr_gantry.mp4");

    fireEvent.click(await screen.findByRole("button", { name: /remove this source/i }));

    expect(await screen.findByText(/still running against this source/i)).toBeInTheDocument();
  });

  it("backing out leaves the source alone", async () => {
    const remove = vi.spyOn(api, "deleteSource").mockResolvedValue(contents());
    renderPanel();
    await screen.findByText(/day_anpr_gantry\.mp4/);
    openDeleteFor("day_anpr_gantry.mp4");

    fireEvent.click(await screen.findByRole("button", { name: /^cancel$/i }));

    expect(remove).not.toHaveBeenCalled();
    expect(screen.queryByTestId("remove-source-summary")).not.toBeInTheDocument();
  });

  it("says when files were left behind", async () => {
    vi.spyOn(api, "deleteSource").mockResolvedValue(contents({ files_removed: false }));
    renderPanel();
    await screen.findByText(/day_anpr_gantry\.mp4/);
    openDeleteFor("day_anpr_gantry.mp4");

    fireEvent.click(await screen.findByRole("button", { name: /remove this source/i }));

    expect(await screen.findByText(/files were left on disk/i)).toBeInTheDocument();
  });
});

describe("SourcePanel: naming a source", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getSourceContents").mockResolvedValue(contents());
  });

  it("names a live stream by its camera, not the last path segment", async () => {
    // Both RTSP sources in the real project display as "1", because
    // that is the channel number at the end of the URL.
    renderPanel();

    expect(await screen.findByText(/160\.187\.179\.196/)).toBeInTheDocument();
    expect(screen.queryByText(/Admin1234/)).not.toBeInTheDocument();
  });

  it("names a video file by its filename", async () => {
    renderPanel();

    expect(await screen.findByText(/day_anpr_gantry\.mp4/)).toBeInTheDocument();
  });
});

describe("SourcePanel: what a source can be asked to do", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getSourceContents").mockResolvedValue(contents());
  });

  it("does not offer Detect + Track on a live stream", async () => {
    // It has no file to decode and no frame count, so the job could
    // only fail - and did, with "frame_count must be positive", on
    // both RTSP sources in the real project.
    renderPanel([stream]);

    await screen.findByText(/160\.187\.179\.196/);
    expect(screen.queryByRole("button", { name: /detect \+ track/i })).not.toBeInTheDocument();
  });

  it("still offers it on a video file", async () => {
    renderPanel([video]);

    expect(await screen.findByRole("button", { name: /detect \+ track/i })).toBeInTheDocument();
  });
});
