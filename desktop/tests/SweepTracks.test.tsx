/**
 * Clearing out the detections nobody accepted.
 *
 * Reviewing is: look at what the model found, accept the ones worth
 * keeping, and then be left with a list of a hundred you do not
 * want. Deleting those one at a time is tidying, not reviewing.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api";
import TrackBrowser from "../src/components/TrackBrowser";
import type { Project, Track, TrackSweep } from "../src/types";

const project: Project = {
  id: "p-1",
  name: "Test",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "anpr-v1",
  workspace_path: "/workspace/p-1",
};

const track = (id: string, review_status: Track["review_status"] = "unreviewed"): Track => ({
  id,
  run_id: "run-1",
  tracker_track_id: 1,
  start_ts: 1870,
  end_ts: 1870,
  bucket: "HARD",
  review_status,
  created_at: "2026-09-19T00:00:00+00:00",
});

const many = [track("t-1", "accepted"), track("t-2"), track("t-3")];

const sweep = (over: Partial<TrackSweep> = {}): TrackSweep => ({
  kept: 1,
  deleted: 111,
  frames_deleted: 111,
  held: 0,
  bytes_freed: 28_000_000,
  ...over,
});

function browser(onSwept = vi.fn()) {
  return render(
    <TrackBrowser
      tracks={many}
      selectedTrackId={null}
      onSelect={vi.fn()}
      project={project}
      onSwept={onSwept}
    />,
  );
}

describe("SweepTracks", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "previewTrackSweep").mockResolvedValue(sweep());
    vi.spyOn(api, "sweepUnacceptedTracks").mockResolvedValue(sweep());
  });

  it("says what it would take before taking it", async () => {
    browser();

    fireEvent.click(screen.getByRole("button", { name: /delete the detections i did not accept/i }));

    const confirm = await screen.findByRole("alert");
    expect(confirm).toHaveTextContent(/111 detection/);
    expect(confirm).toHaveTextContent(/27 MB/);
    expect(confirm).toHaveTextContent(/1.*accepted or flagged stay/i);
    expect(api.sweepUnacceptedTracks).not.toHaveBeenCalled();
  });

  it("deletes only when confirmed, and tells the list to catch up", async () => {
    const onSwept = vi.fn();
    browser(onSwept);
    fireEvent.click(screen.getByRole("button", { name: /delete the detections i did not accept/i }));

    fireEvent.click(await screen.findByRole("button", { name: /cancel/i }));
    expect(api.sweepUnacceptedTracks).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /delete the detections i did not accept/i }));
    fireEvent.click(await screen.findByRole("button", { name: /^delete them$/i }));

    await waitFor(() => expect(api.sweepUnacceptedTracks).toHaveBeenCalledWith(project.id));
    await waitFor(() => expect(onSwept).toHaveBeenCalled());
  });

  it("offers nothing to press when everything has been reviewed", async () => {
    vi.mocked(api.previewTrackSweep).mockResolvedValue(sweep({ deleted: 0, frames_deleted: 0, bytes_freed: 0 }));
    browser();

    fireEvent.click(screen.getByRole("button", { name: /delete the detections i did not accept/i }));

    expect(await screen.findByText(/every detection here has been reviewed/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^delete them$/i })).not.toBeInTheDocument();
  });

  it("says why a detection stayed behind when one did", async () => {
    vi.mocked(api.sweepUnacceptedTracks).mockResolvedValue(sweep({ deleted: 108, held: 3 }));
    browser();
    fireEvent.click(screen.getByRole("button", { name: /delete the detections i did not accept/i }));
    fireEvent.click(await screen.findByRole("button", { name: /^delete them$/i }));

    expect(await screen.findByText(/3 stayed because a dataset version/i)).toBeInTheDocument();
  });

  it("says why it was refused", async () => {
    vi.mocked(api.sweepUnacceptedTracks).mockRejectedValue(
      new ApiError(409, "conflict", "A processing run is still going."),
    );
    browser();
    fireEvent.click(screen.getByRole("button", { name: /delete the detections i did not accept/i }));
    fireEvent.click(await screen.findByRole("button", { name: /^delete them$/i }));

    expect(await screen.findByText(/still going/i)).toBeInTheDocument();
  });

  it("offers nothing at all when there are no detections", () => {
    render(<TrackBrowser tracks={[]} selectedTrackId={null} onSelect={vi.fn()} project={project} />);

    expect(
      screen.queryByRole("button", { name: /delete the detections i did not accept/i }),
    ).not.toBeInTheDocument();
  });
});
