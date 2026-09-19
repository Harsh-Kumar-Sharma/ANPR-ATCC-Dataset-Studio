import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import TrackReview from "../src/components/TrackReview";
import type { Annotation, FrameCandidate, OcrCandidate, Project, Track, TrackTimeline } from "../src/types";

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
  review_status: "unreviewed",
  created_at: "2026-09-19T00:00:00+00:00",
};

const candidate: FrameCandidate = {
  id: "fc-1",
  track_id: "t-1",
  frame_index: 0,
  timestamp_ms: 0,
  image_path: "/nowhere.jpg",
  bbox_json: [10, 10, 60, 40],
  detector_class: "car",
  detector_confidence: 0.9,
  blur_score: null,
  sharpness_score: null,
  area_ratio: null,
  flags_json: { roles: ["best_detection"], quality_score: 0.8, truncated: false },
};

const timeline: TrackTimeline = { track, frames: [candidate] };

function annotation(attributes: Record<string, unknown> = {}): Annotation {
  return {
    id: "a-1",
    frame_id: "f-1",
    frame_candidate_id: "fc-1",
    source: "human",
    class_id: 4,
    bbox_json: [10, 10, 60, 40],
    attributes,
    status: "accepted",
    updated_at: "2026-09-19T00:00:00+00:00",
  };
}

const reading: OcrCandidate = {
  id: "o-1",
  track_id: "t-1",
  frame_candidate_id: "fc-1",
  source: "model",
  plate_bbox_json: [0, 0, 10, 5],
  text: "MH 12 AB 1234",
  normalized_text: "MH12AB1234",
  confidence: 0.92,
  selected: true,
  created_at: "2026-09-19T00:00:00+00:00",
};

function renderReview() {
  return render(
    <TrackReview
      project={project}
      track={track}
      onReviewed={vi.fn()}
      onNavigateTrack={vi.fn()}
      classesVersion={0}
    />,
  );
}

describe("TrackReview: the plate card", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getClassSchema").mockResolvedValue([{ id: 4, name: "Car/Jeep/Van" }]);
    vi.spyOn(api, "getTrackTimeline").mockResolvedValue(timeline);
    vi.spyOn(api, "listOcrCandidates").mockResolvedValue([reading]);
  });

  it("shows the plate already recorded on the label", async () => {
    vi.spyOn(api, "getAnnotation").mockResolvedValue(annotation({ plate_text: "MH12AB1234" }));
    renderReview();

    expect(await screen.findByLabelText<HTMLInputElement>("Plate text")).toHaveValue("MH12AB1234");
  });

  it("shows what the server stored, not what was typed", async () => {
    // The server canonicalises. Echoing the input announced a save that
    // did not happen - and for input that normalises to nothing, it
    // announced a plate it had just wiped.
    vi.spyOn(api, "getAnnotation").mockResolvedValue(annotation());
    vi.spyOn(api, "setTrackPlateText").mockResolvedValue(annotation({ plate_text: "MH12AB1234" }));
    renderReview();

    const field = await screen.findByLabelText("Plate text");
    fireEvent.change(field, { target: { value: "mh 12 ab 1234" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(screen.getByLabelText<HTMLInputElement>("Plate text")).toHaveValue("MH12AB1234"));
    expect(screen.getByText(/plate saved: MH12AB1234/i)).toBeInTheDocument();
  });

  it("says the plate was cleared when the server cleared it", async () => {
    vi.spyOn(api, "getAnnotation").mockResolvedValue(annotation({ plate_text: "MH12AB1234" }));
    vi.spyOn(api, "setTrackPlateText").mockResolvedValue(annotation());
    renderReview();

    const field = await screen.findByLabelText("Plate text");
    fireEvent.change(field, { target: { value: "!!!" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(await screen.findByText(/plate cleared/i)).toBeInTheDocument();
  });

  it("taking the model's reading records it", async () => {
    vi.spyOn(api, "getAnnotation").mockResolvedValue(annotation());
    const save = vi.spyOn(api, "setTrackPlateText").mockResolvedValue(annotation({ plate_text: "MH12AB1234" }));
    renderReview();

    fireEvent.click(await screen.findByRole("button", { name: /^use$/i }));

    await waitFor(() => expect(save).toHaveBeenCalledWith("t-1", "MH12AB1234"));
  });

  it("does not take a plate it has nowhere to put", async () => {
    // There is no annotation until the track is reviewed. Accepting the
    // typing and refusing it on save sent the user to a control that
    // moves to the next track, losing what they had just read.
    vi.spyOn(api, "getAnnotation").mockResolvedValue(null);
    renderReview();

    expect(await screen.findByLabelText<HTMLInputElement>("Plate text")).toBeDisabled();
    expect(screen.getByRole("button", { name: /^use$/i })).toBeDisabled();
    expect(screen.getByText(/accept or flag this track/i)).toBeInTheDocument();
  });

  it("opens the plate field after a failed review too", async () => {
    // A failed review still writes a human annotation, so there is a
    // label to hang a plate on. Treating it as no label left the field
    // disabled on a track that had just been reviewed.
    vi.spyOn(api, "getAnnotation").mockResolvedValue(null);
    vi.spyOn(api, "submitReview").mockResolvedValue({
      track: { ...track, review_status: "failed" },
      annotation: { ...annotation(), status: "failed", class_id: null },
    });
    renderReview();

    await screen.findByLabelText("Plate text");
    fireEvent.click(screen.getByRole("button", { name: /fail/i }));

    await waitFor(() => expect(screen.getByLabelText<HTMLInputElement>("Plate text")).not.toBeDisabled());
  });

  it("opens the plate field once the track has been reviewed", async () => {
    vi.spyOn(api, "getAnnotation").mockResolvedValue(null);
    vi.spyOn(api, "submitReview").mockResolvedValue({
      track: { ...track, review_status: "accepted" },
      annotation: annotation(),
    });
    renderReview();

    await screen.findByLabelText("Plate text");
    fireEvent.change(screen.getByLabelText("Class"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: /accept/i }));

    await waitFor(() => expect(screen.getByLabelText<HTMLInputElement>("Plate text")).not.toBeDisabled());
  });
});
