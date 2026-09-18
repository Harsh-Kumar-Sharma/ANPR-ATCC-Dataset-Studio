import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import TrackReview from "../src/components/TrackReview";
import type { Project, Track, TrackTimeline } from "../src/types";

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

const timeline: TrackTimeline = {
  track,
  frames: [
    {
      id: "fc-1",
      track_id: "t-1",
      frame_index: 0,
      timestamp_ms: 0,
      image_path: "/nowhere.jpg",
      bbox_json: [0, 0, 10, 10],
      detector_class: "car",
      detector_confidence: 0.9,
      blur_score: null,
      sharpness_score: null,
      area_ratio: null,
      flags_json: null,
    },
  ],
};

describe("TrackReview class list", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getTrackTimeline").mockResolvedValue(timeline);
    vi.spyOn(api, "getAnnotation").mockResolvedValue(null as never);
    vi.spyOn(api, "listOcrCandidates").mockResolvedValue([]);
  });

  it("reloads its classes when the editor reports a change", async () => {
    // Ticket 06's done-when: a rename has to reach the panel that is open,
    // not wait for it to be remounted.
    const getClassSchema = vi
      .spyOn(api, "getClassSchema")
      .mockResolvedValueOnce([{ id: 2, name: "number_plate" }])
      .mockResolvedValueOnce([{ id: 2, name: "plate" }]);

    const { rerender } = render(
      <TrackReview
        project={project}
        track={track}
        onReviewed={vi.fn()}
        onNavigateTrack={vi.fn()}
        classesVersion={0}
      />,
    );

    expect(await screen.findByText("number_plate")).toBeInTheDocument();

    rerender(
      <TrackReview
        project={project}
        track={track}
        onReviewed={vi.fn()}
        onNavigateTrack={vi.fn()}
        classesVersion={1}
      />,
    );

    await waitFor(() => expect(getClassSchema).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("plate")).toBeInTheDocument();
  });

  it("does not refetch classes when nothing about them changed", async () => {
    const getClassSchema = vi.spyOn(api, "getClassSchema").mockResolvedValue([{ id: 2, name: "number_plate" }]);

    const { rerender } = render(
      <TrackReview
        project={project}
        track={track}
        onReviewed={vi.fn()}
        onNavigateTrack={vi.fn()}
        classesVersion={3}
      />,
    );
    await screen.findByText("number_plate");

    rerender(
      <TrackReview
        project={project}
        track={track}
        onReviewed={vi.fn()}
        onNavigateTrack={vi.fn()}
        classesVersion={3}
      />,
    );

    expect(getClassSchema).toHaveBeenCalledTimes(1);
  });
});
