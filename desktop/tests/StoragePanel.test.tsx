import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api";
import StoragePanel, { readableSize } from "../src/components/StoragePanel";
import type { Project, StorageUsage } from "../src/types";

const project: Project = {
  id: "p-1",
  name: "ANPR",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "atcc-v1",
  workspace_path: "/workspace/p-1",
};

function usage(overrides: Partial<StorageUsage> = {}): StorageUsage {
  return {
    free_bytes: 9_400_000_000,
    total_bytes: 476_000_000_000,
    app_bytes: 2_500_000_000,
    job_files_bytes: 40_000,
    projects: [
      {
        project_id: "p-1",
        name: "ANPR",
        source_videos_bytes: 900_000_000,
        track_crops_bytes: 1_100_000_000,
        frame_images_bytes: 400_000_000,
        exports_bytes: 100_000_000,
        total_bytes: 2_500_000_000,
      },
    ],
    orphan_workspaces: [{ path: "/workspace/abandoned", bytes: 60_000_000 }],
    ...overrides,
  };
}

describe("StoragePanel: seeing where the disk went", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getStorageUsage").mockResolvedValue(usage());
  });

  it("says how much room is left and how much the app is using", async () => {
    render(<StoragePanel project={project} />);

    const line = await screen.findByTestId("storage-free");
    expect(line.textContent).toContain("8.8 GB");
    expect(line.textContent).toContain("2.3 GB");
  });

  it("breaks this project down by kind", async () => {
    render(<StoragePanel project={project} />);

    const breakdown = await screen.findByTestId("storage-breakdown");
    expect(breakdown.textContent).toContain("Source videos");
    expect(breakdown.textContent).toContain("Track crops");
    expect(breakdown.textContent).toContain("Decoded frames");
    expect(breakdown.textContent).toContain("Exported datasets");
  });

  it("surfaces a failure instead of an empty panel", async () => {
    vi.spyOn(api, "getStorageUsage").mockRejectedValue(new ApiError(500, "boom", "backend down"));
    render(<StoragePanel project={project} />);

    expect(await screen.findByText(/backend down/i)).toBeInTheDocument();
  });
});

describe("StoragePanel: getting it back", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getStorageUsage").mockResolvedValue(usage());
  });

  it("clears decoded frames and says what it freed", async () => {
    const clear = vi
      .spyOn(api, "clearFrameImages")
      .mockResolvedValue({ reclaimed_bytes: 400_000_000, detail: "Cleared decoded frames for 2 source(s)." });
    render(<StoragePanel project={project} />);

    await screen.findByTestId("storage-breakdown");
    fireEvent.click(screen.getAllByRole("button", { name: /^clear$/i })[0]);

    await waitFor(() => expect(clear).toHaveBeenCalledWith("p-1"));
    expect(await screen.findByText(/freed 381 MB/i)).toBeInTheDocument();
  });

  it("will not offer to clear what is already empty", async () => {
    vi.spyOn(api, "getStorageUsage").mockResolvedValue(
      usage({
        projects: [
          {
            project_id: "p-1",
            name: "ANPR",
            source_videos_bytes: 900_000_000,
            track_crops_bytes: 0,
            frame_images_bytes: 0,
            exports_bytes: 0,
            total_bytes: 900_000_000,
          },
        ],
        job_files_bytes: 0,
        orphan_workspaces: [],
      }),
    );
    render(<StoragePanel project={project} />);

    await screen.findByTestId("storage-breakdown");
    for (const button of screen.getAllByRole("button")) {
      expect(button).toBeDisabled();
    }
  });

  it("removes abandoned workspaces", async () => {
    const remove = vi
      .spyOn(api, "removeOrphanWorkspaces")
      .mockResolvedValue({ reclaimed_bytes: 60_000_000, detail: "Removed 1 abandoned workspace(s)." });
    render(<StoragePanel project={project} />);

    fireEvent.click(await screen.findByRole("button", { name: /^remove$/i }));

    await waitFor(() => expect(remove).toHaveBeenCalled());
  });

  it("says plainly that clearing does not remove labels", async () => {
    // The whole reason this is usable mid-run.
    render(<StoragePanel project={project} />);

    expect(await screen.findByText(/never removes a label or a frame/i)).toBeInTheDocument();
  });
});

describe("readableSize", () => {
  it("does not round a size up into the next unit's worth", () => {
    expect(readableSize(1048575)).toBe("1.0 MB");
  });

  it("keeps a decimal for small sizes", () => {
    expect(readableSize(1536)).toBe("1.5 KB");
  });

  it("says so rather than printing NaN", () => {
    expect(readableSize(Number.NaN)).toBe("unknown");
  });
});
