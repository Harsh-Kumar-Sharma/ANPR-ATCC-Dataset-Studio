import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import SourcePanel from "../src/components/SourcePanel";
import { sourceLabel } from "../src/sourceLabel";

const project = {
  id: "p-1",
  name: "ANPR",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "anpr-v1",
  workspace_path: "/workspace/p-1",
};

const live = {
  id: "s-1",
  project_id: "p-1",
  name: null,
  type: "rtsp",
  path_or_uri: "rtsp://admin:pw@160.187.179.196:9001/Streaming/channels/1",
  fps: 10,
  width: 0,
  height: 0,
  frame_count: 0,
  duration_ms: 0,
  created_at: "2026-10-03T00:00:00",
  is_processing: false,
  is_frozen: false,
  ground_truth_vehicle_count: null,
  stored_frames: 25,
};

describe("sourceLabel", () => {
  it("prefers a name someone gave it", () => {
    expect(sourceLabel({ ...live, name: "Toll plaza night" })).toBe("Toll plaza night");
  });

  it("falls back to the camera when unnamed or blank", () => {
    expect(sourceLabel(live)).toBe("160.187.179.196 · ch 1");
    expect(sourceLabel({ ...live, name: "   " })).toBe("160.187.179.196 · ch 1");
  });
});

describe("naming a source", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listSources").mockResolvedValue([live as never]);
    vi.spyOn(api, "listModels").mockResolvedValue([]);
  });

  it("renames from the list and keeps the camera beside the new name", async () => {
    const rename = vi
      .spyOn(api, "renameSource")
      .mockResolvedValue({ ...live, name: "Toll plaza night" } as never);
    render(<SourcePanel project={project} onProcessed={() => {}} onWatch={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: /rename 160\.187/i }));
    const input = screen.getByRole("textbox", { name: /name for 160\.187/i });
    fireEvent.change(input, { target: { value: "Toll plaza night" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(rename).toHaveBeenCalledWith("p-1", "s-1", "Toll plaza night"));
    expect(await screen.findByRole("button", { name: "Toll plaza night" })).toBeInTheDocument();
    expect(screen.getByText("160.187.179.196 · ch 1")).toBeInTheDocument();
  });

  it("Escape leaves the name as it was", async () => {
    const rename = vi.spyOn(api, "renameSource");
    render(<SourcePanel project={project} onProcessed={() => {}} onWatch={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: /rename 160\.187/i }));
    fireEvent.keyDown(screen.getByRole("textbox", { name: /name for/i }), { key: "Escape" });

    expect(screen.getByRole("button", { name: "160.187.179.196 · ch 1" })).toBeInTheDocument();
    expect(rename).not.toHaveBeenCalled();
  });
});

describe("removing a source from a long list", () => {
  it("asks right under the source, not after the whole list", async () => {
    vi.restoreAllMocks();
    const second = { ...live, id: "s-2", name: "Lane 2" };
    vi.spyOn(api, "listSources").mockResolvedValue([live as never, second as never]);
    vi.spyOn(api, "listModels").mockResolvedValue([]);
    vi.spyOn(api, "getSourceContents").mockResolvedValue({
      frames: 0, tracks: 0, labels: 0, bytes: 0, running_jobs: 0, files_removed: false,
    });
    render(<SourcePanel project={project} onProcessed={() => {}} onWatch={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: /remove 160\.187/i }));

    const item = screen.getByRole("button", { name: "160.187.179.196 · ch 1" }).closest("li") as HTMLElement;
    expect(await within(item).findByRole("button", { name: /remove this source/i })).toBeInTheDocument();
    const other = screen.getByRole("button", { name: "Lane 2" }).closest("li") as HTMLElement;
    expect(within(other).queryByRole("button", { name: /remove this source/i })).not.toBeInTheDocument();
  });
});
