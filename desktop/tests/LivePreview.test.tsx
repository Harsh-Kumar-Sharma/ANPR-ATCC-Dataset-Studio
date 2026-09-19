/**
 * What the live preview claims about speed.
 *
 * The badge used to show how often the preview image changed, next to
 * the word LIVE, where it read as throughput. It is throttled to
 * about 8 a second on the backend and polled at 10 a second here, so
 * it settled around 6 - and someone who had typed 30 into "Expected
 * FPS" reasonably concluded the pipeline was crawling.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import LivePreview from "../src/components/LivePreview";
import type { RtspSessionStatus } from "../src/types";

const status = (over: Partial<RtspSessionStatus> = {}): RtspSessionStatus => ({
  run_id: "run-1",
  connected: true,
  reconnect_attempts: 0,
  frames_captured: 0,
  frames_dropped: 0,
  tracks_persisted: 0,
  frames_saved: 0,
  stopped: false,
  error: null,
  ...over,
});

describe("LivePreview: what the rate means", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // The preview image never arrives; the rate must not depend on it.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 204, headers: new Headers() }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("measures the camera from the session's own counter, not from the preview", async () => {
    // Ten frames per second of session time, while the preview image
    // never arrives at all. The rate must come out as the camera's.
    const start = performance.now();
    let polls = 0;
    vi.spyOn(performance, "now").mockImplementation(() => start + polls * 1000);
    vi.spyOn(api, "getRtspStatus").mockImplementation(async () => {
      polls += 1;
      return status({ frames_captured: polls * 10 });
    });

    render(<LivePreview runId="run-1" onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText(/10\.0 fps from camera/)).toBeInTheDocument(), {
      timeout: 5000,
    });
  });

  it("says the rate is the camera's, not a setting", async () => {
    vi.spyOn(api, "getRtspStatus").mockResolvedValue(status({ frames_captured: 100 }));

    render(<LivePreview runId="run-1" onClose={vi.fn()} />);

    expect(await screen.findByText(/not a setting/i)).toBeInTheDocument();
    expect(screen.getByText(/refreshes more slowly/i)).toBeInTheDocument();
  });

  it("shows no rate at all until it has measured one", async () => {
    // A number invented from a single sample is worse than no number.
    vi.spyOn(api, "getRtspStatus").mockResolvedValue(status({ frames_captured: 500 }));

    render(<LivePreview runId="run-1" onClose={vi.fn()} />);
    await waitFor(() => expect(api.getRtspStatus).toHaveBeenCalled());

    expect(screen.queryByText(/fps/i)).not.toBeInTheDocument();
  });
});
