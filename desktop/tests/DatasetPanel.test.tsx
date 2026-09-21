import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import DatasetPanel from "../src/components/DatasetPanel";
import type { DatasetExportResult, Project } from "../src/types";

const project: Project = {
  id: "p-1",
  name: "Test",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "anpr-v1",
  workspace_path: "/workspace/p-1",
};

function exportResult(overrides: Partial<DatasetExportResult> = {}): DatasetExportResult {
  return {
    dataset_version: {
      id: "dv-1",
      project_id: "p-1",
      version: 1,
      created_at: "2026-09-19T00:00:00+00:00",
      split_seed: 42,
      config_snapshot_json: {},
    },
    counts: { train: 40, val: 5, test: 5, total: 50 },
    object_counts: { train: 60, val: 8, test: 7, total: 75 },
    background_frames: 0,
    frames_with_unclassified_boxes: 0,
    frames_skipped_unclassified: 0,
    frames_skipped_unrecoverable: 0,
    validation: { valid: true, errors: [], warnings: [] },
    ...overrides,
  };
}

describe("DatasetPanel: what an export reports", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listDatasetVersions").mockResolvedValue([]);
  });

  it("reports images and boxes as the different numbers they are", async () => {
    // Fifty labelled frames holding seventy-five boxes is not "75
    // frames". The two counts came from the same place until a frame
    // could carry more than one box.
    vi.spyOn(api, "exportDataset").mockResolvedValue(exportResult());
    render(<DatasetPanel project={project} />);

    fireEvent.click(screen.getByRole("button", { name: /export dataset version/i }));

    const message = await screen.findByText(/50 full frame\(s\)/);
    expect(message.textContent).toContain("75 box(es)");
  });

  it("says when work was left out, so a shrinking export is not a mystery", async () => {
    // Fifty frames labelled, forty-five exported, and nothing anywhere
    // saying why was the previous behaviour.
    vi.spyOn(api, "exportDataset").mockResolvedValue(exportResult({ frames_skipped_unclassified: 5 }));
    render(<DatasetPanel project={project} />);

    fireEvent.click(screen.getByRole("button", { name: /export dataset version/i }));

    expect(await screen.findByText(/5 labelled frame\(s\) left out/)).toBeInTheDocument();
  });

  it("says when frames were labelled as holding nothing", async () => {
    // A background image is deliberate, and a dataset quietly full of
    // them is something the labeller should be able to see.
    vi.spyOn(api, "exportDataset").mockResolvedValue(exportResult({ background_frames: 6 }));
    render(<DatasetPanel project={project} />);

    fireEvent.click(screen.getByRole("button", { name: /export dataset version/i }));

    // Phrased as a subset of the images, not as extra ones.
    expect(await screen.findByText(/6 full frame\(s\) \(6 labelled empty\)|\(6 labelled empty\)/)).toBeInTheDocument();
  });

  it("does not mention empty frames when there are none", async () => {
    vi.spyOn(api, "exportDataset").mockResolvedValue(exportResult());
    render(<DatasetPanel project={project} />);

    fireEvent.click(screen.getByRole("button", { name: /export dataset version/i }));

    await screen.findByText(/50 full frame\(s\)/);
    expect(screen.queryByText(/labelled empty/)).not.toBeInTheDocument();
  });

  it("surfaces every validation warning rather than only counting them", async () => {
    vi.spyOn(api, "exportDataset").mockResolvedValue(
      exportResult({
        validation: {
          valid: true,
          errors: [],
          warnings: ["3 exported frame(s) contain detected vehicles with no accepted annotation."],
        },
      }),
    );
    render(<DatasetPanel project={project} />);

    fireEvent.click(screen.getByRole("button", { name: /export dataset version/i }));

    expect(await screen.findByText(/no accepted annotation/)).toBeInTheDocument();
  });
});

describe("DatasetPanel: frames whose image is gone", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listDatasetVersions").mockResolvedValue([]);
  });

  it("says how many were left out, and why", async () => {
    // Two frames of a live stream that was never recorded used to take
    // the whole export down. Now they are skipped - but skipping in
    // silence would be the same work quietly not landing.
    vi.spyOn(api, "exportDataset").mockResolvedValue(
      exportResult({ frames_skipped_unrecoverable: 2 }),
    );
    render(<DatasetPanel project={project} />);

    fireEvent.click(screen.getByRole("button", { name: /export dataset version/i }));

    expect(await screen.findByText(/2 labelled frame\(s\) left out - their image is gone/i)).toBeTruthy();
  });

  it("says nothing about them when none were lost", async () => {
    vi.spyOn(api, "exportDataset").mockResolvedValue(exportResult());
    render(<DatasetPanel project={project} />);

    fireEvent.click(screen.getByRole("button", { name: /export dataset version/i }));

    await screen.findByText(/validation passed/i);
    expect(screen.queryByText(/image is gone/i)).toBeNull();
  });
});
