import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import TrackReview from "../src/components/TrackReview";
import type { FrameCandidate, Project, Track, TrackTimeline } from "../src/types";

/**
 * Reviewing a detection means seeing the frame it is on.
 *
 * A tight cut-out of a number plate is not something a person can
 * judge, and it is not what a labelling dataset is made of.
 */

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
  bucket: "BEST_DETECTION",
  review_status: "unreviewed",
  created_at: "2026-09-19T00:00:00+00:00",
};

function candidate(overrides: Partial<FrameCandidate> = {}): FrameCandidate {
  return {
    id: "fc-1",
    track_id: "t-1",
    frame_id: "f-1",
    full_frame: true,
    frame_index: 7,
    timestamp_ms: 700,
    image_path: "/nowhere.jpg",
    bbox_json: [10, 10, 60, 40],
    detector_class: "plate",
    detector_confidence: 0.9,
    blur_score: null,
    sharpness_score: null,
    area_ratio: null,
    flags_json: { roles: ["best_detection"], quality_score: 0.8, truncated: false },
    ...overrides,
  };
}

function renderWith(frame: FrameCandidate) {
  const timeline: TrackTimeline = { track, frames: [frame] };
  vi.spyOn(api, "getTrackTimeline").mockResolvedValue(timeline);
  return render(
    <TrackReview project={project} track={track} onReviewed={vi.fn()} onNavigateTrack={vi.fn()} />,
  );
}

describe("TrackReview: what it shows you", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getClassSchema").mockResolvedValue([{ id: 4, name: "plate" }]);
    vi.spyOn(api, "getAnnotation").mockResolvedValue(null);
    vi.spyOn(api, "listOcrCandidates").mockResolvedValue([]);
  });

  it("shows the whole frame the detection sits on", async () => {
    renderWith(candidate());

    const image = await screen.findByAltText("Frame 7");
    await waitFor(() => expect(image).toHaveAttribute("src", api.fullFrameImageUrl("f-1")));
  });

  it("falls back to the crop when the frame cannot be recovered", async () => {
    // A live stream cannot be decoded twice. A frame that was never
    // written has no pixels left, and a broken image is worse than a
    // small one.
    renderWith(candidate({ full_frame: false }));

    const image = await screen.findByAltText("Frame 7");
    await waitFor(() => expect(image).toHaveAttribute("src", api.frameImageUrl("fc-1")));
  });
});
