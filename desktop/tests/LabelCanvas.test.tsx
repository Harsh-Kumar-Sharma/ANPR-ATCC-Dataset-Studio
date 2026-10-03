import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api";
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

const classes = [
  { id: 1, name: "vehicle" },
  { id: 2, name: "number_plate" },
];

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

/** A drag starts on some element and, like a real one, finishes wherever
 *  the mouse is released - the window, not necessarily the image. */
function drag(target: Element, from: [number, number], to: [number, number]) {
  fireEvent.mouseDown(target, { button: 0, clientX: from[0], clientY: from[1] });
  fireEvent.mouseMove(window, { clientX: to[0], clientY: to[1] });
  fireEvent.mouseUp(window, { clientX: to[0], clientY: to[1] });
}

function key(k: string, init: KeyboardEventInit = {}) {
  fireEvent.keyDown(window, { key: k, ...init });
}

function geometry(rect: Element) {
  return ["x", "y", "width", "height"].map((attr) => Number(rect.getAttribute(attr)));
}

const box = (i = 0) => screen.getAllByTestId("label-box")[i];
const drawn = (bbox: [number, number, number, number]) => ({ id: null, class_id: null, bbox_json: bbox, attributes: {} });

function renderCanvas(props: Partial<React.ComponentProps<typeof LabelCanvas>> = {}) {
  return render(<LabelCanvas project={project} frame={frame} {...props} />);
}

describe("LabelCanvas: drawing", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getFramePlateReadings").mockResolvedValue([]);
    // Nothing here is about attributes, but an unmocked call would go
    // out to a real backend that is not running.
    vi.spyOn(api, "listAttributeDefinitions").mockResolvedValue([]);
    vi.spyOn(api, "getClassSchema").mockResolvedValue(classes);
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([]);
    drawnAtHalfSize();
  });

  it("shows the boxes already on the frame, in frame pixels", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation([10, 10, 50, 50]), annotation([100, 100, 200, 150])]);

    renderCanvas();

    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(2));
    expect(geometry(box(0))).toEqual([10, 10, 40, 40]);
    expect(screen.getByText(/2 boxes/)).toBeInTheDocument();
  });

  it("draws a box by dragging on empty space, mapping screen pixels to frame pixels", async () => {
    renderCanvas();
    await screen.findByText(/0 boxes/);

    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);

    expect(geometry(box())).toEqual([20, 20, 80, 60]);
    expect(screen.getByText(/1 box \(unsaved\)/)).toBeInTheDocument();
  });

  it("normalises a box dragged from bottom-right to top-left", async () => {
    renderCanvas();
    await screen.findByText(/0 boxes/);

    drag(screen.getByTestId("label-stage"), [50, 40], [10, 10]);

    expect(geometry(box())).toEqual([20, 20, 80, 60]);
  });

  it("ignores a click that does not become a box", async () => {
    renderCanvas();
    await screen.findByText(/0 boxes/);

    drag(screen.getByTestId("label-stage"), [10, 10], [10, 10]);

    expect(screen.queryAllByTestId("label-box")).toHaveLength(0);
    expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
  });

  it("keeps drawing when the pointer leaves the image, clamping to the edge", async () => {
    renderCanvas();
    await screen.findByText(/0 boxes/);

    drag(screen.getByTestId("label-stage"), [300, 230], [900, 900]);

    expect(geometry(box())).toEqual([600, 460, 40, 20]);
  });

  it("a newly drawn box is selected and has no class yet", async () => {
    renderCanvas();
    await screen.findByText(/0 boxes/);

    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);

    expect(box()).toHaveAttribute("data-selected", "true");
    expect(screen.getByLabelText(/class of selected box/i)).toHaveValue("");
    expect(screen.getByTestId("label-class")).toHaveTextContent("?");
  });
});

describe("LabelCanvas: selecting and cycling", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getFramePlateReadings").mockResolvedValue([]);
    // Nothing here is about attributes, but an unmocked call would go
    // out to a real backend that is not running.
    vi.spyOn(api, "listAttributeDefinitions").mockResolvedValue([]);
    vi.spyOn(api, "getClassSchema").mockResolvedValue(classes);
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([
      annotation([10, 10, 50, 50], 1),
      annotation([100, 100, 200, 150], 2),
      annotation([300, 300, 400, 400], null),
    ]);
    drawnAtHalfSize();
  });

  it("clicking a box selects it and shows its class", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(3));

    fireEvent.mouseDown(box(1), { button: 0, clientX: 60, clientY: 60 });
    fireEvent.mouseUp(window, { clientX: 60, clientY: 60 });

    expect(box(1)).toHaveAttribute("data-selected", "true");
    expect(box(0)).toHaveAttribute("data-selected", "false");
    expect(screen.getByText(/box 2 of 3/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/class of selected box/i)).toHaveValue("2");
  });

  it("] and [ cycle through the boxes, wrapping", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(3));

    key("]");
    expect(box(0)).toHaveAttribute("data-selected", "true");
    key("]");
    key("]");
    expect(box(2)).toHaveAttribute("data-selected", "true");
    key("]");
    expect(box(0)).toHaveAttribute("data-selected", "true");
    key("[");
    expect(box(2)).toHaveAttribute("data-selected", "true");
  });

  it("Escape deselects", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(3));
    key("]");
    expect(box(0)).toHaveAttribute("data-selected", "true");

    key("Escape");

    expect(box(0)).toHaveAttribute("data-selected", "false");
    expect(screen.queryByLabelText(/class of selected box/i)).not.toBeInTheDocument();
  });

  it("shows each box's class name on the overlay", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-class")).toHaveLength(3));

    expect(screen.getAllByTestId("label-class").map((t) => t.textContent)).toEqual(["vehicle", "number_plate", "?"]);
  });
});

describe("LabelCanvas: classes", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getFramePlateReadings").mockResolvedValue([]);
    // Nothing here is about attributes, but an unmocked call would go
    // out to a real backend that is not running.
    vi.spyOn(api, "listAttributeDefinitions").mockResolvedValue([]);
    vi.spyOn(api, "getClassSchema").mockResolvedValue(classes);
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation([10, 10, 50, 50], null)]);
    drawnAtHalfSize();
  });

  it("a number key assigns the class at that position in the project's list", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));
    key("]");

    key("2");

    expect(screen.getByLabelText(/class of selected box/i)).toHaveValue("2");
    expect(screen.getByTestId("label-class")).toHaveTextContent("number_plate");
    expect(screen.getByText(/unsaved/)).toBeInTheDocument();
  });

  it("0 clears the class", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));
    key("]");
    key("1");
    expect(screen.getByLabelText(/class of selected box/i)).toHaveValue("1");

    key("0");

    expect(screen.getByLabelText(/class of selected box/i)).toHaveValue("");
  });

  it("a number with no class at that position does nothing", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));
    key("]");

    key("7");

    expect(screen.getByLabelText(/class of selected box/i)).toHaveValue("");
    expect(screen.queryByText(/unsaved/)).not.toBeInTheDocument();
  });

  it("the select assigns a class too, and typing in it does not trigger shortcuts", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));
    key("]");
    const select = screen.getByLabelText(/class of selected box/i);

    fireEvent.change(select, { target: { value: "1" } });
    expect(screen.getByTestId("label-class")).toHaveTextContent("vehicle");

    // A key press aimed at the select is the select's business.
    fireEvent.keyDown(select, { key: "Delete" });
    expect(screen.getAllByTestId("label-box")).toHaveLength(1);
  });

  it("reloads the class list when the editor reports a change", async () => {
    const getClassSchema = vi.spyOn(api, "getClassSchema").mockResolvedValue(classes);
    const { rerender } = renderCanvas({ classesVersion: 0 });
    await waitFor(() => expect(getClassSchema).toHaveBeenCalledTimes(1));

    rerender(<LabelCanvas project={project} frame={frame} classesVersion={1} />);

    await waitFor(() => expect(getClassSchema).toHaveBeenCalledTimes(2));
  });
});

describe("LabelCanvas: editing", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getFramePlateReadings").mockResolvedValue([]);
    // Nothing here is about attributes, but an unmocked call would go
    // out to a real backend that is not running.
    vi.spyOn(api, "listAttributeDefinitions").mockResolvedValue([]);
    vi.spyOn(api, "getClassSchema").mockResolvedValue(classes);
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation([100, 100, 200, 150], 1)]);
    drawnAtHalfSize();
  });

  it("Delete removes the selected box", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));
    key("]");

    key("Delete");

    expect(screen.queryAllByTestId("label-box")).toHaveLength(0);
    expect(screen.getByText(/0 boxes \(unsaved\)/)).toBeInTheDocument();
  });

  it("the delete button removes the selected box too", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));
    key("]");

    fireEvent.click(screen.getByRole("button", { name: /delete selected box/i }));

    expect(screen.queryAllByTestId("label-box")).toHaveLength(0);
  });

  it("Delete with nothing selected does nothing", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));

    key("Delete");

    expect(screen.getAllByTestId("label-box")).toHaveLength(1);
  });

  it("arrow keys nudge the selected box by one frame pixel, Shift by ten", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));
    key("]");

    key("ArrowRight");
    key("ArrowDown");
    expect(geometry(box())).toEqual([101, 101, 100, 50]);

    key("ArrowLeft", { shiftKey: true });
    key("ArrowUp", { shiftKey: true });
    expect(geometry(box())).toEqual([91, 91, 100, 50]);
  });

  it("nudging stops at the frame edge without shrinking the box", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation([600, 0, 640, 40], 1)]);
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));
    key("]");

    key("ArrowRight", { shiftKey: true });

    expect(geometry(box())).toEqual([600, 0, 40, 40]);
  });

  it("dragging a box's body moves it", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));

    // Grab at frame (120,120) [screen 60,60], release 20 screen px right and down.
    drag(box(), [60, 60], [80, 80]);

    expect(geometry(box())).toEqual([140, 140, 100, 50]);
    expect(screen.getByText(/unsaved/)).toBeInTheDocument();
  });

  it("moving a box clamps so it stays inside the frame", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));

    drag(box(), [60, 60], [900, 900]);

    expect(geometry(box())).toEqual([540, 430, 100, 50]);
  });

  it("dragging a corner handle resizes the box", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));
    key("]");

    const se = screen.getAllByTestId("label-handle").find((h) => h.getAttribute("data-handle") === "se")!;
    // se corner is at frame (200,150) [screen 100,75]; drag to frame (260,190).
    drag(se, [100, 75], [130, 95]);

    expect(geometry(box())).toEqual([100, 100, 160, 90]);
  });

  it("dragging an edge handle resizes only that edge", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));
    key("]");

    const w = screen.getAllByTestId("label-handle").find((h) => h.getAttribute("data-handle") === "w")!;
    // west edge at frame x=100 [screen 50]; drag to frame x=60.
    drag(w, [50, 62], [30, 62]);

    expect(geometry(box())).toEqual([60, 100, 140, 50]);
  });

  it("resizing past the opposite edge flips rather than inverting", async () => {
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));
    key("]");

    const e = screen.getAllByTestId("label-handle").find((h) => h.getAttribute("data-handle") === "e")!;
    // east edge at frame x=200 [screen 100]; drag left past the west edge to frame x=40.
    drag(e, [100, 62], [20, 62]);

    expect(geometry(box())).toEqual([40, 100, 60, 50]);
  });

  it("a resize cannot collapse a box to nothing", async () => {
    // The server rejects a zero-area box (0 <= x1 < x2), so a handle
    // dragged onto the opposite edge must stop short rather than produce
    // something that fails on save.
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));
    key("]");

    const e = screen.getAllByTestId("label-handle").find((h) => h.getAttribute("data-handle") === "e")!;
    // Drag the east edge exactly onto the west edge at frame x=100 [screen 50].
    drag(e, [100, 62], [50, 62]);

    const [x, , width] = geometry(box());
    expect(width).toBeGreaterThanOrEqual(2);
    expect(x).toBe(100);
  });

  it("a resize cannot collapse a box against the frame edge either", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation([0, 0, 100, 50], 1)]);
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));
    key("]");

    const w = screen.getAllByTestId("label-handle").find((h) => h.getAttribute("data-handle") === "w")!;
    // West edge is already at 0; drag it right onto the east edge.
    drag(w, [0, 12], [50, 12]);

    const [, , width] = geometry(box());
    expect(width).toBeGreaterThanOrEqual(2);
  });

  it("Escape during a drag puts the box back where it was", async () => {
    // apply() has already written the dragged geometry by the time Escape
    // arrives, so cancelling has to restore the original - otherwise the
    // box stays moved while Save sits disabled and the edit is stranded.
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));
    const before = geometry(box());

    fireEvent.mouseDown(box(), { button: 0, clientX: 60, clientY: 60 });
    fireEvent.mouseMove(window, { clientX: 90, clientY: 90 });
    expect(geometry(box())).not.toEqual(before);

    key("Escape");

    expect(geometry(box())).toEqual(before);
    expect(screen.queryByText(/unsaved/)).not.toBeInTheDocument();
  });

  it("deleting selects the next box rather than dropping the user out", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([
      annotation([10, 10, 50, 50], 1),
      annotation([100, 100, 200, 150], 2),
    ]);
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(2));
    key("]");

    key("Delete");

    expect(screen.getAllByTestId("label-box")).toHaveLength(1);
    expect(screen.getByText(/box 1 of 1/i)).toBeInTheDocument();
  });

  it("handles only appear on the selected box", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation([10, 10, 50, 50]), annotation([100, 100, 200, 150])]);
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(2));

    expect(screen.queryAllByTestId("label-handle")).toHaveLength(0);
    key("]");
    expect(screen.getAllByTestId("label-handle")).toHaveLength(8);
  });
});

describe("LabelCanvas: saving", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getFramePlateReadings").mockResolvedValue([]);
    // Nothing here is about attributes, but an unmocked call would go
    // out to a real backend that is not running.
    vi.spyOn(api, "listAttributeDefinitions").mockResolvedValue([]);
    vi.spyOn(api, "getClassSchema").mockResolvedValue(classes);
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([]);
    drawnAtHalfSize();
  });

  it("S saves when there is something to save", async () => {
    renderCanvas();
    await screen.findByText(/0 boxes/);
    const save = vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([annotation([20, 20, 100, 80], null)]);

    key("s");
    expect(save).not.toHaveBeenCalled();

    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);
    key("s");

    await waitFor(() => expect(save).toHaveBeenCalledWith("f-1", [drawn([20, 20, 100, 80])]));
    await waitFor(() => expect(screen.queryByText(/unsaved/)).not.toBeInTheDocument());
  });

  it("saves the complete set, sending loaded boxes back with their ids", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation([10, 10, 50, 50], 2)]);
    renderCanvas();
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

  it("labels a frame with four vehicles entirely from the keyboard after drawing", async () => {
    // Ticket 09's done-when: draw four, then class every one without the
    // mouse, and save.
    renderCanvas();
    await screen.findByText(/0 boxes/);
    const stage = screen.getByTestId("label-stage");
    drag(stage, [10, 10], [30, 30]);
    drag(stage, [40, 10], [60, 30]);
    drag(stage, [70, 10], [90, 30]);
    drag(stage, [100, 10], [120, 30]);
    expect(screen.getAllByTestId("label-box")).toHaveLength(4);

    // The last drawn box is selected; walk back through them assigning classes.
    key("1");
    key("[");
    key("2");
    key("[");
    key("1");
    key("[");
    key("2");

    const save = vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([]);
    key("s");

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("f-1", [
        { id: null, class_id: 2, bbox_json: [20, 20, 60, 60], attributes: {} },
        { id: null, class_id: 1, bbox_json: [80, 20, 120, 60], attributes: {} },
        { id: null, class_id: 2, bbox_json: [140, 20, 180, 60], attributes: {} },
        { id: null, class_id: 1, bbox_json: [200, 20, 240, 60], attributes: {} },
      ]),
    );
  });

  it("tells the app a frame was saved", async () => {
    const onSaved = vi.fn();
    renderCanvas({ onSaved });
    await screen.findByText(/0 boxes/);
    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);
    vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([annotation([20, 20, 100, 80], null)]);

    key("s");

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: "f-1", status: "labeled" })));
  });

  it("shows the server's reason when a save is refused, keeping the work", async () => {
    renderCanvas();
    await screen.findByText(/0 boxes/);
    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);
    vi.spyOn(api, "saveFrameAnnotations").mockRejectedValue(
      new ApiError(409, "stale_box", "1 box(es) refer to annotations no longer on this frame. Reload it and try again."),
    );

    key("s");

    expect(await screen.findByRole("alert")).toHaveTextContent(/reload it and try again/i);
    expect(screen.getAllByTestId("label-box")).toHaveLength(1);
    expect(screen.getByText(/unsaved/)).toBeInTheDocument();
  });

  it("does not throw away an edit made while a save is in flight", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation([10, 10, 50, 50], 1)]);
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(1));

    let release: (value: Annotation[]) => void = () => {};
    vi.spyOn(api, "saveFrameAnnotations").mockReturnValue(
      new Promise<Annotation[]>((resolve) => {
        release = resolve;
      }),
    );
    key("]");
    key("2");
    key("s");

    // The user keeps working while the request is out.
    key("ArrowRight");
    const nudged = geometry(box());

    release([annotation([10, 10, 50, 50], 2)]);
    await waitFor(() => expect(screen.getByRole("button", { name: /^save$/i })).toBeEnabled());

    // The nudge survived, and the frame still knows it has work to send.
    expect(geometry(box())).toEqual(nudged);
    expect(screen.getByText(/unsaved/)).toBeInTheDocument();
  });

  it("keeps the same box selected when the server returns them reordered", async () => {
    // The server orders by updated_at, so an edited box can come back in a
    // different position. A positional selection would silently land on
    // someone else's box.
    const first = annotation([10, 10, 50, 50], 1);
    const second = annotation([100, 100, 200, 150], 2);
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([first, second]);
    renderCanvas();
    await waitFor(() => expect(screen.getAllByTestId("label-box")).toHaveLength(2));

    key("]");
    key("2");
    expect(screen.getByText(/box 1 of 2/i)).toBeInTheDocument();

    // Edited box comes back last.
    vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([second, { ...first, class_id: 2 }]);
    key("s");

    await waitFor(() => expect(screen.queryByText(/unsaved/)).not.toBeInTheDocument());
    expect(screen.getByText(/box 1 of 2/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/class of selected box/i)).toHaveValue("2");
    expect(geometry(box(0))).toEqual([10, 10, 40, 40]);
  });

  it("skips a frame that has nothing unsaved on it, without asking", async () => {
    const setStatus = vi
      .spyOn(api, "setFrameStatus")
      .mockResolvedValue({ ...frame, status: "rejected" });
    const onRejected = vi.fn();
    renderCanvas({ onRejected });
    await screen.findByText(/0 boxes/);

    fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));

    await waitFor(() => expect(setStatus).toHaveBeenCalledWith("f-1", "rejected"));
    await waitFor(() => expect(onRejected).toHaveBeenCalled());
  });

  it("asks before skipping a frame with unsaved boxes", async () => {
    // Skipping throws the boxes away, so it gets the same courtesy as
    // walking away from the frame does.
    const setStatus = vi.spyOn(api, "setFrameStatus").mockResolvedValue({ ...frame, status: "rejected" });
    renderCanvas();
    await screen.findByText(/0 boxes/);
    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);

    fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));

    expect(setStatus).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /skip and lose boxes/i })).toBeInTheDocument();
  });

  it("goes through with the skip when told again", async () => {
    const setStatus = vi.spyOn(api, "setFrameStatus").mockResolvedValue({ ...frame, status: "rejected" });
    renderCanvas();
    await screen.findByText(/0 boxes/);
    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);
    fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));

    fireEvent.click(screen.getByRole("button", { name: /skip and lose boxes/i }));

    await waitFor(() => expect(setStatus).toHaveBeenCalledWith("f-1", "rejected"));
  });

  it("lets the user call off a skip and keep the boxes", async () => {
    const setStatus = vi.spyOn(api, "setFrameStatus").mockResolvedValue({ ...frame, status: "rejected" });
    renderCanvas();
    await screen.findByText(/0 boxes/);
    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);
    fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));

    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    expect(setStatus).not.toHaveBeenCalled();
    expect(screen.getAllByTestId("label-box")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /^skip$/i })).toBeInTheDocument();
  });

  it("tells the app when the frame gains and loses unsaved boxes", async () => {
    const onDirtyChange = vi.fn();
    const { unmount } = renderCanvas({ onDirtyChange });
    await screen.findByText(/0 boxes/);
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);

    drag(screen.getByTestId("label-stage"), [10, 10], [50, 40]);
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);

    // On the way out the boxes are gone, so nothing should still think
    // there is work to lose.
    unmount();
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });

  it("lists the shortcuts where the user can see them", async () => {
    renderCanvas();
    await screen.findByText(/0 boxes/);

    const hints = screen.getByTestId("label-shortcuts").textContent ?? "";
    for (const k of ["[", "]", "1", "9", "0", "Del", "S", "Esc"]) expect(hints).toContain(k);
  });
});

describe("LabelCanvas: opening with what the model found", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getFramePlateReadings").mockResolvedValue([]);
    vi.spyOn(api, "listAttributeDefinitions").mockResolvedValue([]);
    vi.spyOn(api, "getClassSchema").mockResolvedValue(classes);
    drawnAtHalfSize();
  });

  it("starts from the model's boxes when nothing is saved yet", async () => {
    // The frame used to open completely empty next to a review
    // screen that already knew exactly where the plate was.
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([]);
    vi.spyOn(api, "getFrameSuggestions").mockResolvedValue([
      { frame_candidate_id: "fc-1", bbox_json: [10, 20, 90, 60], detector_class: "plate", detector_confidence: 0.8 },
    ]);

    render(<LabelCanvas project={project} frame={frame} />);

    expect(await screen.findByTestId("label-box")).toBeInTheDocument();
    expect(await screen.findByText(/from the model/i)).toBeInTheDocument();
  });

  it("prefers what was saved over what the model thinks", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation([10, 10, 60, 40])]);
    const suggestions = vi.spyOn(api, "getFrameSuggestions").mockResolvedValue([
      { frame_candidate_id: "fc-1", bbox_json: [0, 0, 5, 5], detector_class: "plate", detector_confidence: 0.9 },
    ]);

    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findByTestId("label-box");
    expect(suggestions).toHaveBeenCalled();
    expect(screen.queryByText(/from the model/i)).not.toBeInTheDocument();
  });

  it("opens empty rather than erroring when suggestions cannot be had", async () => {
    // Drawing by hand still works, which is what matters.
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([]);
    vi.spyOn(api, "getFrameSuggestions").mockRejectedValue(new Error("offline"));

    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findByTestId("label-stage");
    expect(screen.queryByTestId("label-box")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("LabelCanvas: an image that will not load", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getFramePlateReadings").mockResolvedValue([]);
    vi.spyOn(api, "listAttributeDefinitions").mockResolvedValue([]);
    vi.spyOn(api, "getClassSchema").mockResolvedValue(classes);
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([]);
    vi.spyOn(api, "getFrameSuggestions").mockResolvedValue([]);
  });

  it("says so instead of showing a black frame", async () => {
    renderCanvas();
    await screen.findByText(/0 boxes/);

    fireEvent.error(screen.getByRole("img"));

    expect(await screen.findByText(/could not be loaded/i)).toBeInTheDocument();
  });
});
