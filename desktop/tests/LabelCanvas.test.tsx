import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api";
import LabelCanvas from "../src/components/LabelCanvas";
import type { Annotation, Frame } from "../src/types";

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
 *  screen pixel is two frame pixels - exactly the mapping the canvas has
 *  to get right. */
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

/** A drag starts on the stage and, like a real one, finishes wherever
 *  the mouse is released - the window, not necessarily the image. */
function drag(stage: HTMLElement, from: [number, number], to: [number, number]) {
  fireEvent.mouseDown(stage, { button: 0, clientX: from[0], clientY: from[1] });
  fireEvent.mouseMove(window, { clientX: to[0], clientY: to[1] });
  fireEvent.mouseUp(window, { clientX: to[0], clientY: to[1] });
}

function geometry(rect: HTMLElement) {
  return ["x", "y", "width", "height"].map((attr) => Number(rect.getAttribute(attr)));
}

const drawn = (bbox: [number, number, number, number]) => ({ id: null, class_id: null, bbox_json: bbox, attributes: {} });

describe("LabelCanvas", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([]);
    drawnAtHalfSize();
  });

  it("shows the boxes already on the frame, in frame pixels", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation([10, 10, 50, 50]), annotation([100, 100, 200, 150])]);

    render(<LabelCanvas frame={frame} />);

    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(2));
    // The overlay's viewBox is the frame, so a box is drawn at its own
    // coordinates and the browser scales it - nothing to keep in sync.
    expect(geometry(screen.getAllByTestId("label-box")[0])).toEqual([10, 10, 40, 40]);
    expect(screen.getByText(/2 boxes/)).toBeInTheDocument();
  });

  it("draws a box by dragging, mapping screen pixels to frame pixels", async () => {
    render(<LabelCanvas frame={frame} />);
    await screen.findByText(/0 boxes/);

    // 10..50 on a half-size image is 20..100 on the frame.
    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);

    expect(geometry(screen.getByTestId("label-box"))).toEqual([20, 20, 80, 60]);
    expect(screen.getByText(/1 box \(unsaved\)/)).toBeInTheDocument();
    expect(screen.getByText(/1 without a class yet/)).toBeInTheDocument();

    const save = vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([annotation([20, 20, 100, 80], null)]);
    screen.getByRole("button", { name: /^save$/i }).click();

    await waitFor(() => expect(save).toHaveBeenCalledWith("f-1", [drawn([20, 20, 100, 80])]));
    await waitFor(() => expect(screen.queryByText(/unsaved/)).not.toBeInTheDocument());
  });

  it("normalises a box dragged from bottom-right to top-left", async () => {
    render(<LabelCanvas frame={frame} />);
    await screen.findByText(/0 boxes/);

    drag(screen.getByTestId("label-stage"), [50, 40], [10, 10]);

    expect(geometry(screen.getByTestId("label-box"))).toEqual([20, 20, 80, 60]);
  });

  it("ignores a click that does not become a box", async () => {
    render(<LabelCanvas frame={frame} />);
    await screen.findByText(/0 boxes/);

    drag(screen.getByTestId("label-stage"), [10, 10], [10, 10]);

    expect(screen.queryAllByTestId("label-box")).toHaveLength(0);
    expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
  });

  it("keeps drawing when the pointer leaves the image, clamping to the edge", async () => {
    // Leaving the stage used to cancel the draft, so a drag past the
    // edge lost the box. The drag is tracked on the window instead.
    render(<LabelCanvas frame={frame} />);
    await screen.findByText(/0 boxes/);

    drag(screen.getByTestId("label-stage"), [300, 230], [900, 900]);

    expect(geometry(screen.getByTestId("label-box"))).toEqual([600, 460, 40, 20]);
  });

  it("shows the draft while dragging", async () => {
    render(<LabelCanvas frame={frame} />);
    await screen.findByText(/0 boxes/);

    fireEvent.mouseDown(screen.getByTestId("label-stage"), { button: 0, clientX: 10, clientY: 10 });
    fireEvent.mouseMove(window, { clientX: 30, clientY: 30 });

    expect(geometry(screen.getByTestId("label-draft"))).toEqual([20, 20, 40, 40]);

    fireEvent.mouseUp(window, { clientX: 30, clientY: 30 });
    expect(screen.queryByTestId("label-draft")).not.toBeInTheDocument();
  });

  it("saves the complete set, sending loaded boxes back with their ids", async () => {
    // An echoed id is updated in place server-side; a box without one is
    // new. This is what keeps an unchanged exported box its own row.
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation([10, 10, 50, 50], 2)]);
    render(<LabelCanvas frame={frame} />);
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));

    drag(screen.getByTestId("label-stage"), [100, 100], [150, 150]);

    const save = vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([]);
    screen.getByRole("button", { name: /^save$/i }).click();

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("f-1", [
        { id: "a-10-10-50-50", class_id: 2, bbox_json: [10, 10, 50, 50], attributes: {} },
        drawn([200, 200, 300, 300]),
      ]),
    );
    await waitFor(() => expect(screen.getByText(/0 boxes/)).toBeInTheDocument());
  });

  it("adopts the ids the server assigned after a save", async () => {
    render(<LabelCanvas frame={frame} />);
    await screen.findByText(/0 boxes/);
    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);

    const save = vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([annotation([20, 20, 100, 80], null)]);
    screen.getByRole("button", { name: /^save$/i }).click();
    await waitFor(() => expect(screen.queryByText(/unsaved/)).not.toBeInTheDocument());

    // Saving again sends the box as itself, not as a new one.
    drag(screen.getByTestId("label-stage"), [100, 100], [150, 150]);
    screen.getByRole("button", { name: /^save$/i }).click();

    await waitFor(() =>
      expect(save).toHaveBeenLastCalledWith("f-1", [
        { id: "a-20-20-100-80", class_id: null, bbox_json: [20, 20, 100, 80], attributes: {} },
        drawn([200, 200, 300, 300]),
      ]),
    );
  });

  it("tells the app a frame was saved and stops being dirty", async () => {
    const onSaved = vi.fn();
    render(<LabelCanvas frame={frame} onSaved={onSaved} />);
    await screen.findByText(/0 boxes/);
    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);

    vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([annotation([20, 20, 100, 80], null)]);
    screen.getByRole("button", { name: /^save$/i }).click();

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: "f-1", status: "labeled" })));
    await waitFor(() => expect(screen.queryByText(/unsaved/)).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
  });

  it("shows the server's reason when a save is refused, keeping the work", async () => {
    render(<LabelCanvas frame={frame} />);
    await screen.findByText(/0 boxes/);
    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);

    vi.spyOn(api, "saveFrameAnnotations").mockRejectedValue(
      new ApiError(409, "stale_box", "1 box(es) refer to annotations no longer on this frame. Reload it and try again."),
    );
    screen.getByRole("button", { name: /^save$/i }).click();

    expect(await screen.findByRole("alert")).toHaveTextContent(/reload it and try again/i);
    expect(screen.getAllByTestId("label-box")).toHaveLength(1);
    expect(screen.getByText(/unsaved/)).toBeInTheDocument();
  });
});
