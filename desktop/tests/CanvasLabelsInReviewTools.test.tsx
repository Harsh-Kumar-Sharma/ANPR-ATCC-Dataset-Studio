import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import ActiveLearningPanel from "../src/components/ActiveLearningPanel";
import LabelBalancePanel from "../src/components/LabelBalancePanel";
import type { DisagreementItem, LabelBalance, Project, Track } from "../src/types";

const project: Project = {
  id: "p-1",
  name: "Test",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "anpr-v1",
  workspace_path: "/workspace/p-1",
};

const track: Track = {
  id: "t-1",
  run_id: "r-1",
  tracker_track_id: 1,
  start_ts: 0,
  end_ts: 100,
  bucket: null,
  review_status: "accepted",
  created_at: "2026-09-19T00:00:00+00:00",
};

const fromTrackReview: DisagreementItem = {
  kind: "class_mismatch",
  frame_id: "f-1",
  annotation_id: "a-1",
  human_class_id: 8,
  human_class_name: "Bus",
  detector_class: "car",
  track_id: "t-1",
};

const fromCanvas: DisagreementItem = {
  kind: "class_mismatch",
  frame_id: "f-2",
  annotation_id: "a-2",
  human_class_id: 8,
  human_class_name: "Bus",
  detector_class: "car",
  track_id: null,
};

const unmatched: DisagreementItem = {
  kind: "unmatched_box",
  frame_id: "f-3",
  annotation_id: "a-3",
  human_class_id: 4,
  human_class_name: "Car/Jeep/Van",
  detector_class: null,
  track_id: null,
};

const balance: LabelBalance = {
  classes: [
    { class_id: 4, name: "Car/Jeep/Van", box_count: 42 },
    { class_id: 8, name: "Bus", box_count: 7 },
  ],
  total_boxes: 49,
  unclassified_boxes: 0,
  labeled_frames: 30,
  background_frames: 2,
};

function renderPanel(overrides: Partial<React.ComponentProps<typeof ActiveLearningPanel>> = {}) {
  return render(
    <ActiveLearningPanel
      project={project}
      tracks={[track]}
      onSelectTrack={vi.fn()}
      onSelectFrame={vi.fn()}
      {...overrides}
    />,
  );
}

function openDisagreements() {
  fireEvent.click(screen.getByRole("button", { name: /disagreements/i }));
}

describe("ActiveLearningPanel: canvas labels in the disagreement queue", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("offers the frame for a canvas box, which has no track to open", async () => {
    // The old panel called onSelectTrack with the item's track_id. For a
    // canvas box that is null, so the button went nowhere.
    const onSelectFrame = vi.fn();
    vi.spyOn(api, "getDisagreements").mockResolvedValue([fromCanvas]);
    renderPanel({ onSelectFrame });

    openDisagreements();
    fireEvent.click(await screen.findByRole("button", { name: /open frame/i }));

    expect(onSelectFrame).toHaveBeenCalledWith("f-2");
  });

  it("says so when the frame cannot be opened, instead of doing nothing", async () => {
    // The only surface that can report it - the app has no toast - so a
    // swallowed failure was a button that silently did nothing.
    vi.spyOn(api, "getDisagreements").mockResolvedValue([fromCanvas]);
    renderPanel({ onSelectFrame: vi.fn().mockRejectedValue(new Error("frame is gone")) });

    openDisagreements();
    fireEvent.click(await screen.findByRole("button", { name: /open frame/i }));

    expect(await screen.findByText(/could not open that frame/i)).toBeInTheDocument();
  });

  it("still opens the track for a label written by reviewing one", async () => {
    const onSelectTrack = vi.fn();
    vi.spyOn(api, "getDisagreements").mockResolvedValue([fromTrackReview]);
    renderPanel({ onSelectTrack });

    openDisagreements();
    fireEvent.click(await screen.findByRole("button", { name: /review track/i }));

    expect(onSelectTrack).toHaveBeenCalledWith(track);
  });

  it("says nothing matched rather than inventing a detector class", async () => {
    // "the detector found nothing here" was a claim the data does not
    // support: a box drawn far enough from its own detection lands here
    // too, and telling the user the model missed it would be false.
    vi.spyOn(api, "getDisagreements").mockResolvedValue([unmatched]);
    renderPanel();

    openDisagreements();

    expect(await screen.findByText(/nothing the detector found matches/i)).toBeInTheDocument();
    expect(screen.queryByText(/detector saw/i)).not.toBeInTheDocument();
  });

  it("describes a class mismatch with both classes", async () => {
    vi.spyOn(api, "getDisagreements").mockResolvedValue([fromCanvas]);
    renderPanel();

    openDisagreements();

    const row = await screen.findByText(/detector saw/i);
    expect(row.textContent).toContain("car");
    expect(row.textContent).toContain("Bus");
  });

  it("an empty queue over real labels says everything agrees", async () => {
    vi.spyOn(api, "getDisagreements").mockResolvedValue([]);
    vi.spyOn(api, "getLabelBalance").mockResolvedValue(balance);
    renderPanel();

    openDisagreements();

    expect(await screen.findByText(/nothing to flag/i)).toBeInTheDocument();
  });

  it("an empty queue over no labels says there is nothing to compare", async () => {
    // Otherwise the panel gives a project nobody has labelled a clean
    // bill of health - the exact conflation this ticket objected to,
    // one sentence further on.
    vi.spyOn(api, "getDisagreements").mockResolvedValue([]);
    vi.spyOn(api, "getLabelBalance").mockResolvedValue({
      classes: [],
      total_boxes: 0,
      unclassified_boxes: 0,
      labeled_frames: 0,
      background_frames: 0,
    });
    renderPanel();

    openDisagreements();

    expect(await screen.findByText(/nothing labelled yet/i)).toBeInTheDocument();
  });
});


describe("LabelBalancePanel: what the labeller has produced", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows the class balance of the whole project", async () => {
    // The evaluation report answers this per processing run, which a
    // canvas box does not belong to. This is the question a labeller
    // actually asks.
    vi.spyOn(api, "getLabelBalance").mockResolvedValue(balance);
    render(<LabelBalancePanel project={project} />);

    expect(await screen.findByText("Car/Jeep/Van")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("Bus")).toBeInTheDocument();
  });

  it("reports frames and background frames alongside the boxes", async () => {
    vi.spyOn(api, "getLabelBalance").mockResolvedValue(balance);
    render(<LabelBalancePanel project={project} />);

    const summary = await screen.findByTestId("label-balance-summary");
    expect(summary.textContent).toContain("49");
    expect(summary.textContent).toContain("30");
    expect(summary.textContent).toContain("2");
  });

  it("calls out boxes still waiting for a class, because they do not export", async () => {
    vi.spyOn(api, "getLabelBalance").mockResolvedValue({ ...balance, unclassified_boxes: 5 });
    render(<LabelBalancePanel project={project} />);

    expect(await screen.findByText(/5 box\(es\) still need a class/i)).toBeInTheDocument();
  });

  it("says nothing about unclassified boxes when there are none", async () => {
    vi.spyOn(api, "getLabelBalance").mockResolvedValue(balance);
    render(<LabelBalancePanel project={project} />);

    await screen.findByText("Car/Jeep/Van");
    expect(screen.queryByText(/still need a class/i)).not.toBeInTheDocument();
  });

  it("tells an unlabelled project to go and label something", async () => {
    vi.spyOn(api, "getLabelBalance").mockResolvedValue({
      classes: [],
      total_boxes: 0,
      unclassified_boxes: 0,
      labeled_frames: 0,
      background_frames: 0,
    });
    render(<LabelBalancePanel project={project} />);

    expect(await screen.findByText(/no labels yet/i)).toBeInTheDocument();
  });

  it("surfaces a failure instead of rendering an empty balance", async () => {
    vi.spyOn(api, "getLabelBalance").mockRejectedValue(new Error("backend down"));
    render(<LabelBalancePanel project={project} />);

    expect(await screen.findByText(/backend down/i)).toBeInTheDocument();
  });
});
