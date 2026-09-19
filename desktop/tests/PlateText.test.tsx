import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api";
import LabelCanvas from "../src/components/LabelCanvas";
import type { Annotation, AttributeDefinition, Frame, PlateReading, Project } from "../src/types";

const project: Project = {
  id: "p-1",
  name: "Test",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "anpr-v1",
  workspace_path: "/workspace/p-1",
};

const frame: Frame = {
  id: "f-1",
  source_id: "s-1",
  frame_index: 12,
  timestamp_ms: 1200,
  width: 640,
  height: 480,
  status: "pending",
  selection_reason: null,
};

const definitions: AttributeDefinition[] = [
  { key: "plate_text", label: "Plate text", type: "text", max_length: 32, placeholder: "e.g. MH12AB1234" },
];

const readings: PlateReading[] = [
  { text: "MH 12 AB 1234", normalized_text: "MH12AB1234", confidence: 0.92 },
  { text: "dl3c9999", normalized_text: "DL3C9999", confidence: 0.41 },
];

function annotation(id: string, attributes: Record<string, unknown> = {}): Annotation {
  return {
    id,
    frame_id: "f-1",
    frame_candidate_id: null,
    source: "human",
    class_id: 1,
    bbox_json: [10, 10, 100, 100],
    attributes,
    status: "accepted",
    updated_at: "2026-09-19T00:00:00+00:00",
  };
}

function selectFirstBox() {
  fireEvent.mouseDown(screen.getAllByTestId("label-box")[0], { button: 0, clientX: 20, clientY: 20 });
  fireEvent.mouseUp(window, { clientX: 20, clientY: 20 });
}

describe("LabelCanvas: what the model read", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getClassSchema").mockResolvedValue([{ id: 1, name: "vehicle" }]);
    vi.spyOn(api, "listAttributeDefinitions").mockResolvedValue(definitions);
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation("a-1")]);
  });

  it("offers the plates the model read on this frame", async () => {
    // Without this a labeller retypes, character by character, a plate
    // the model already has - and gets it wrong differently.
    vi.spyOn(api, "getFramePlateReadings").mockResolvedValue(readings);
    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findAllByTestId("label-box");
    selectFirstBox();

    const suggestions = await screen.findByTestId("plate-readings");
    expect(suggestions.textContent).toContain("MH12AB1234");
    expect(suggestions.textContent).toContain("DL3C9999");
  });

  it("clicking one fills the plate field with it", async () => {
    vi.spyOn(api, "getFramePlateReadings").mockResolvedValue(readings);
    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findAllByTestId("label-box");
    selectFirstBox();
    fireEvent.click(await screen.findByRole("button", { name: "MH12AB1234" }));

    expect(screen.getByLabelText<HTMLInputElement>("Plate text")).toHaveValue("MH12AB1234");
  });

  it("clicking one counts as unsaved work, like typing it would", async () => {
    const onDirtyChange = vi.fn();
    vi.spyOn(api, "getFramePlateReadings").mockResolvedValue(readings);
    render(<LabelCanvas project={project} frame={frame} onDirtyChange={onDirtyChange} />);

    await screen.findAllByTestId("label-box");
    selectFirstBox();
    fireEvent.click(await screen.findByRole("button", { name: "MH12AB1234" }));

    await waitFor(() => expect(onDirtyChange).toHaveBeenCalledWith(true));
  });

  it("says nothing when the model read nothing", async () => {
    vi.spyOn(api, "getFramePlateReadings").mockResolvedValue([]);
    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findAllByTestId("label-box");
    selectFirstBox();

    await screen.findByLabelText("Plate text");
    expect(screen.queryByTestId("plate-readings")).not.toBeInTheDocument();
  });

  it("still labels the frame when the readings cannot be loaded", async () => {
    // Suggestions are a convenience; boxes are the job.
    vi.spyOn(api, "getFramePlateReadings").mockRejectedValue(new ApiError(500, "boom", "backend down"));
    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findAllByTestId("label-box");
    selectFirstBox();

    expect(await screen.findByLabelText("Plate text")).toBeInTheDocument();
    expect(screen.queryByTestId("plate-readings")).not.toBeInTheDocument();
  });
});
