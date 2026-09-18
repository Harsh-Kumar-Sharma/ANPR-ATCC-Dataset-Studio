import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import LabelCanvas from "../src/components/LabelCanvas";
import type { Annotation, Frame, Project } from "../src/types";

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

function annotation(bbox: [number, number, number, number], class_id: number | null = 1): Annotation {
  return {
    id: `a-${bbox.join("-")}`,
    frame_id: "f-1",
    frame_candidate_id: null,
    source: "human",
    class_id,
    bbox_json: bbox,
    attributes: {},
    status: "accepted",
    updated_at: "2026-09-19T00:00:00+00:00",
  };
}

/** jsdom has no layout. The image is told it is drawn at half size, so a
 *  screen pixel is two frame pixels - which is exactly the mapping the
 *  canvas has to get right. */
function drawnAtHalfSize() {
  vi.spyOn(HTMLImageElement.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0,
    top: 0,
    width: 320,
    height: 240,
    right: 320,
    bottom: 240,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
}

function drag(stage: HTMLElement, from: [number, number], to: [number, number]) {
  fireEvent.mouseDown(stage, { button: 0, clientX: from[0], clientY: from[1] });
  fireEvent.mouseMove(stage, { clientX: to[0], clientY: to[1] });
  fireEvent.mouseUp(stage, { clientX: to[0], clientY: to[1] });
}

describe("LabelCanvas", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getClassSchema").mockResolvedValue([
      { id: 1, name: "vehicle" },
      { id: 2, name: "number_plate" },
    ]);
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([]);
    drawnAtHalfSize();
  });

  it("shows the boxes already on the frame", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation([10, 10, 50, 50]), annotation([100, 100, 200, 150])]);

    render(<LabelCanvas project={project} frame={frame} />);

    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(2));
    expect(screen.getByText(/2 boxes/)).toBeInTheDocument();
  });

  it("draws a box by dragging, in full-frame pixels", async () => {
    render(<LabelCanvas project={project} frame={frame} />);
    await screen.findByText(/0 boxes/);

    // 10..50 on a half-size image is 20..100 on the frame.
    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);

    expect(screen.getAllByTestId("label-box")).toHaveLength(1);
    expect(screen.getByText(/1 box \(unsaved\)/)).toBeInTheDocument();

    const save = vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([annotation([20, 20, 100, 80])]);
    screen.getByRole("button", { name: /^save$/i }).click();

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("f-1", [{ class_id: 1, bbox_json: [20, 20, 100, 80], attributes: {} }]),
    );
  });

  it("normalises a box dragged from bottom-right to top-left", async () => {
    render(<LabelCanvas project={project} frame={frame} />);
    await screen.findByText(/0 boxes/);

    drag(screen.getByTestId("label-stage"), [50, 40], [10, 10]);

    const save = vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([]);
    screen.getByRole("button", { name: /^save$/i }).click();

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("f-1", [{ class_id: 1, bbox_json: [20, 20, 100, 80], attributes: {} }]),
    );
  });

  it("ignores a click that does not become a box", async () => {
    render(<LabelCanvas project={project} frame={frame} />);
    await screen.findByText(/0 boxes/);

    drag(screen.getByTestId("label-stage"), [10, 10], [10, 10]);

    expect(screen.queryAllByTestId("label-box")).toHaveLength(0);
    expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
  });

  it("clamps a drag that leaves the image to the frame edge", async () => {
    render(<LabelCanvas project={project} frame={frame} />);
    await screen.findByText(/0 boxes/);

    drag(screen.getByTestId("label-stage"), [300, 230], [900, 900]);

    const save = vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([]);
    screen.getByRole("button", { name: /^save$/i }).click();

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("f-1", [{ class_id: 1, bbox_json: [600, 460, 640, 480], attributes: {} }]),
    );
  });

  it("saves the complete set, existing boxes included", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation([10, 10, 50, 50], 2)]);
    render(<LabelCanvas project={project} frame={frame} />);
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));

    drag(screen.getByTestId("label-stage"), [100, 100], [150, 150]);

    const save = vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([]);
    screen.getByRole("button", { name: /^save$/i }).click();

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("f-1", [
        { class_id: 2, bbox_json: [10, 10, 50, 50], attributes: {} },
        { class_id: 1, bbox_json: [200, 200, 300, 300], attributes: {} },
      ]),
    );
  });

  it("tells the app a frame was saved and stops being dirty", async () => {
    const onSaved = vi.fn();
    render(<LabelCanvas project={project} frame={frame} onSaved={onSaved} />);
    await screen.findByText(/0 boxes/);
    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);

    vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([annotation([20, 20, 100, 80])]);
    screen.getByRole("button", { name: /^save$/i }).click();

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: "f-1", status: "labeled" })));
    expect(screen.queryByText(/unsaved/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
  });

  it("shows the server's reason when a save is refused", async () => {
    const { ApiError } = await import("../src/api");
    render(<LabelCanvas project={project} frame={frame} />);
    await screen.findByText(/0 boxes/);
    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);

    vi.spyOn(api, "saveFrameAnnotations").mockRejectedValue(
      new ApiError(400, "invalid_box", "Box 0: class 1 is not one of this project's classes."),
    );
    screen.getByRole("button", { name: /^save$/i }).click();

    expect(await screen.findByRole("alert")).toHaveTextContent(/Box 0: class 1/);
    // The unsaved work is still on screen to be fixed, not thrown away.
    expect(screen.getAllByTestId("label-box")).toHaveLength(1);
  });
});
