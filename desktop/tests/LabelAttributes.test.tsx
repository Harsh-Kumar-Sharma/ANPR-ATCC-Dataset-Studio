import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import LabelCanvas from "../src/components/LabelCanvas";
import type { Annotation, AttributeDefinition, Frame, Project } from "../src/types";

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

const definitions: AttributeDefinition[] = [
  { key: "plate_text", label: "Plate text", type: "text", max_length: 32, placeholder: "e.g. MH 12 AB 1234" },
  {
    key: "direction",
    label: "Direction",
    type: "choice",
    options: [
      { value: "incoming", label: "Incoming" },
      { value: "outgoing", label: "Outgoing" },
    ],
  },
  { key: "occluded", label: "Occluded", type: "boolean" },
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

describe("LabelCanvas: the attributes panel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getClassSchema").mockResolvedValue(classes);
    vi.spyOn(api, "listAttributeDefinitions").mockResolvedValue(definitions);
  });

  it("shows nothing until a box is selected", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation("a-1")]);
    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findAllByTestId("label-box");
    expect(screen.queryByLabelText("Plate text")).not.toBeInTheDocument();
  });

  it("renders a control per attribute for the selected box", async () => {
    // Rendered from the server's list, not from a copy in here - a copy
    // is how the UI ends up offering a value the save refuses.
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation("a-1")]);
    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findAllByTestId("label-box");
    selectFirstBox();

    expect(await screen.findByLabelText("Plate text")).toBeInTheDocument();
    expect(screen.getByLabelText("Direction")).toBeInTheDocument();
    expect(screen.getByLabelText("Occluded")).toBeInTheDocument();
  });

  it("shows what the selected box already carries", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([
      annotation("a-1", { plate_text: "MH 12 AB 1234", direction: "outgoing", occluded: true }),
    ]);
    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findAllByTestId("label-box");
    selectFirstBox();

    expect(await screen.findByLabelText<HTMLInputElement>("Plate text")).toHaveValue("MH 12 AB 1234");
    expect(screen.getByLabelText<HTMLSelectElement>("Direction")).toHaveValue("outgoing");
    expect(screen.getByLabelText<HTMLInputElement>("Occluded")).toBeChecked();
  });

  it("sends typed plate text on save", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation("a-1")]);
    const save = vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([
      annotation("a-1", { plate_text: "DL 3C 1234" }),
    ]);
    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findAllByTestId("label-box");
    selectFirstBox();
    fireEvent.change(await screen.findByLabelText("Plate text"), { target: { value: "DL 3C 1234" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("f-1", [
        expect.objectContaining({ id: "a-1", attributes: { plate_text: "DL 3C 1234" } }),
      ]),
    );
  });

  it("sends a boolean, not a string, for a checkbox", async () => {
    // The server refuses "true". Getting this wrong would make the panel
    // unusable rather than subtly wrong, which is the good outcome, but
    // it is worth pinning.
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation("a-1")]);
    const save = vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([annotation("a-1", { occluded: true })]);
    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findAllByTestId("label-box");
    selectFirstBox();
    fireEvent.click(await screen.findByLabelText("Occluded"));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("f-1", [expect.objectContaining({ attributes: { occluded: true } })]),
    );
  });

  it("clearing a field drops the attribute rather than sending a blank", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation("a-1", { plate_text: "KA 01 AA 1111" })]);
    const save = vi.spyOn(api, "saveFrameAnnotations").mockResolvedValue([annotation("a-1")]);
    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findAllByTestId("label-box");
    selectFirstBox();
    fireEvent.change(await screen.findByLabelText("Plate text"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(save).toHaveBeenCalledWith("f-1", [expect.objectContaining({ attributes: {} })]));
  });

  it("editing an attribute counts as unsaved work", async () => {
    // Otherwise the queue walks away from a typed plate without asking.
    const onDirtyChange = vi.fn();
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation("a-1")]);
    render(<LabelCanvas project={project} frame={frame} onDirtyChange={onDirtyChange} />);

    await screen.findAllByTestId("label-box");
    selectFirstBox();
    fireEvent.change(await screen.findByLabelText("Plate text"), { target: { value: "TN 09 BC 4321" } });

    await waitFor(() => expect(onDirtyChange).toHaveBeenCalledWith(true));
  });

  it("typing a plate does not fire the box shortcuts", async () => {
    // "1" is assign-class-one and "]" is next-box. Both would be a
    // disaster inside a plate number.
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation("a-1")]);
    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findAllByTestId("label-box");
    selectFirstBox();
    const field = await screen.findByLabelText("Plate text");
    fireEvent.change(field, { target: { value: "MH 12" } });
    fireEvent.keyDown(field, { key: "1", bubbles: true });

    expect(screen.getByLabelText<HTMLInputElement>("Plate text")).toHaveValue("MH 12");
    expect(screen.getByLabelText<HTMLSelectElement>("Class of selected box")).toHaveValue("1");
  });

  it("each box keeps its own attributes", async () => {
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([
      { ...annotation("a-1", { plate_text: "FIRST BOX" }), bbox_json: [10, 10, 100, 100] },
      { ...annotation("a-2", { plate_text: "SECOND BOX" }), bbox_json: [200, 200, 300, 300] },
    ]);
    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findAllByTestId("label-box");
    selectFirstBox();
    expect(await screen.findByLabelText<HTMLInputElement>("Plate text")).toHaveValue("FIRST BOX");

    fireEvent.keyDown(window, { key: "]" });
    await waitFor(() => expect(screen.getByLabelText<HTMLInputElement>("Plate text")).toHaveValue("SECOND BOX"));
  });

  it("still lets a box be labelled when the attribute list cannot be loaded", async () => {
    // A definitions endpoint that is down must not take the canvas with
    // it - classes and boxes are the job, attributes are the extra.
    vi.spyOn(api, "listAttributeDefinitions").mockRejectedValue(new Error("boom"));
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([annotation("a-1")]);
    render(<LabelCanvas project={project} frame={frame} />);

    await screen.findAllByTestId("label-box");
    selectFirstBox();

    expect(await screen.findByLabelText("Class of selected box")).toBeInTheDocument();
    expect(screen.queryByLabelText("Plate text")).not.toBeInTheDocument();
  });
});
