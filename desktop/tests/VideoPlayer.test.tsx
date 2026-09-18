import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import VideoPlayer from "../src/components/VideoPlayer";
import type { Project, Source, SourceDetections, Track } from "../src/types";

const getSourceDetections = vi.fn();

vi.mock("../src/api", () => ({
  api: {
    getSourceDetections: (...args: unknown[]) => getSourceDetections(...args),
    sourceVideoUrl: () => "http://backend.test/video",
  },
}));

// jsdom has no ResizeObserver. This fake lets a test decide exactly when
// and to what size the player "resizes", instead of depending on a real
// browser's layout and frame timing.
class FakeResizeObserver {
  static active: FakeResizeObserver[] = [];
  private readonly callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }

  observe() {
    FakeResizeObserver.active.push(this);
  }

  unobserve() {}

  disconnect() {
    FakeResizeObserver.active = FakeResizeObserver.active.filter((o) => o !== this);
  }

  static resizeTo(width: number, height: number) {
    act(() => {
      for (const observer of FakeResizeObserver.active) {
        observer.callback(
          [{ contentRect: { width, height } } as unknown as ResizeObserverEntry],
          observer as unknown as ResizeObserver,
        );
      }
    });
  }
}

const project = { id: "p1", name: "Project" } as unknown as Project;
const source = {
  id: "s1",
  project_id: "p1",
  type: "video",
  path_or_uri: "data/workspace/p1/source/clip.3gp",
  width: 1920,
  height: 1080,
  fps: 8,
  duration_ms: 10_000,
  frame_count: 80,
} as unknown as Source;

const track: Track = {
  id: "t1",
  run_id: "r1",
  tracker_track_id: 7,
  start_ts: 0,
  end_ts: 0,
  bucket: "BEST_DETECTION",
  review_status: "unreviewed",
  created_at: "2026-09-10T10:00:00",
} as Track;

function detectionsWithOneBox(): SourceDetections {
  return {
    source_id: "s1",
    width: 1920,
    height: 1080,
    fps: 8,
    run_id: "r1",
    runs: [{ id: "r1", status: "completed", started_at: "2026-09-10T10:00:00", track_count: 1 }],
    frames: [
      {
        frame_index: 0,
        timestamp_ms: 0,
        boxes: [
          {
            track_id: "t1",
            tracker_track_id: 7,
            bbox: [100, 200, 500, 600],
            detector_class: "car",
            confidence: 0.91,
            bucket: "BEST_DETECTION",
            review_status: "unreviewed",
          },
        ],
      },
    ],
  };
}

function renderPlayer(onSelectTrack = vi.fn()) {
  const utils = render(
    <VideoPlayer project={project} source={source} tracks={[track]} onSelectTrack={onSelectTrack} onClose={vi.fn()} />,
  );
  return { ...utils, onSelectTrack };
}

function labelFontSize(container: HTMLElement): number {
  const text = container.querySelector("svg.player-overlay text");
  if (!text) throw new Error("no label rendered");
  return Number(text.getAttribute("font-size"));
}

describe("VideoPlayer", () => {
  beforeEach(() => {
    FakeResizeObserver.active = [];
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    // Take the requestVideoFrameCallback path; it never fires here, so the
    // playhead stays at 0 where the test's detection frame is.
    Object.assign(HTMLVideoElement.prototype, {
      requestVideoFrameCallback: vi.fn(() => 1),
      cancelVideoFrameCallback: vi.fn(),
    });
    getSourceDetections.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("draws the box at its full-frame coordinates with a readable label", async () => {
    getSourceDetections.mockResolvedValue(detectionsWithOneBox());
    const { container } = renderPlayer();

    expect(await screen.findByText("#7 car 91%")).toBeInTheDocument();
    const rect = container.querySelector("svg.player-overlay rect")!;
    expect(container.querySelector("svg.player-overlay")!.getAttribute("viewBox")).toBe("0 0 1920 1080");
    expect([rect.getAttribute("x"), rect.getAttribute("y"), rect.getAttribute("width"), rect.getAttribute("height")]).toEqual([
      "100",
      "200",
      "400",
      "400",
    ]);
  });

  it("keeps the label a constant on-screen size as the player is resized", async () => {
    getSourceDetections.mockResolvedValue(detectionsWithOneBox());
    const { container } = renderPlayer();
    await screen.findByText("#7 car 91%");

    // A 640x360 player shows 1920x1080 video at 1/3 scale, so a 13px label
    // needs 39 viewBox units. Sizing it in video pixels instead made it ~8px.
    FakeResizeObserver.resizeTo(640, 360);
    expect(labelFontSize(container)).toBeCloseTo(39, 5);

    FakeResizeObserver.resizeTo(1920, 1080);
    expect(labelFontSize(container)).toBeCloseTo(13, 5);

    // Height-limited: letterboxing uses the smaller of the two scales.
    FakeResizeObserver.resizeTo(960, 800);
    expect(labelFontSize(container)).toBeCloseTo(26, 5);
  });

  it("opens the matching track when a box is clicked", async () => {
    getSourceDetections.mockResolvedValue(detectionsWithOneBox());
    const { container, onSelectTrack } = renderPlayer();
    await screen.findByText("#7 car 91%");

    fireEvent.click(container.querySelector("svg.player-overlay .det-box")!);
    expect(onSelectTrack).toHaveBeenCalledWith(track);
  });

  it("marks each tracked vehicle on the timeline", async () => {
    getSourceDetections.mockResolvedValue(detectionsWithOneBox());
    const { container } = renderPlayer();
    await screen.findByText("#7 car 91%");

    expect(container.querySelectorAll(".tl-marker")).toHaveLength(1);
  });

  it("explains how to get boxes when the video has not been processed", async () => {
    getSourceDetections.mockResolvedValue({ ...detectionsWithOneBox(), run_id: null, runs: [], frames: [] });
    const { container } = renderPlayer();

    expect(await screen.findByText(/No detections for this video yet/)).toBeInTheDocument();
    expect(container.querySelector("svg.player-overlay")).toBeNull();
  });
});
