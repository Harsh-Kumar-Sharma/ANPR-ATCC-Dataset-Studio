import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import JobIndicator from "../src/components/JobIndicator";
import JobsPanel from "../src/components/JobsPanel";
import type { Job } from "../src/types";

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-1",
    project_id: "p-1",
    type: "detect",
    status: "running",
    progress: 0.42,
    progress_message: "Frame 42 of 100",
    result_json: null,
    error_message: null,
    pid: 1234,
    created_at: "2026-09-19T00:00:00+00:00",
    started_at: "2026-09-19T00:00:01+00:00",
    completed_at: null,
    ...overrides,
  };
}

describe("JobsPanel", () => {
  it("says so plainly when nothing has run", () => {
    render(<JobsPanel jobs={[]} />);
    expect(screen.getByText(/nothing has run yet/i)).toBeInTheDocument();
  });

  it("shows a running job's progress and what it is doing", () => {
    render(<JobsPanel jobs={[job()]} />);

    expect(screen.getByText("Frame 42 of 100")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
  });

  it("surfaces a failed job's error instead of hiding it", () => {
    // The whole reason failed jobs stay in the list: this used to
    // disappear into a 500 with nothing left to read.
    render(<JobsPanel jobs={[job({ status: "failed", error_message: "could not decode frame 12" })]} />);

    expect(screen.getByText(/could not decode frame 12/)).toBeInTheDocument();
  });

  it("drops the progress bar once a job is finished", () => {
    render(<JobsPanel jobs={[job({ status: "succeeded", progress: 1, completed_at: "2026-09-19T00:05:00+00:00" })]} />);

    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByText("Done")).toBeInTheDocument();
  });

  it("lists every job it is given", () => {
    render(<JobsPanel jobs={[job({ id: "a" }), job({ id: "b", status: "succeeded" })]} />);

    expect(screen.getAllByTestId("job-row")).toHaveLength(2);
  });
});

describe("JobIndicator", () => {
  it("stays out of the way when nothing is running", () => {
    render(<JobIndicator activeJobs={[]} />);
    expect(screen.queryByTestId("job-indicator")).not.toBeInTheDocument();
  });

  it("shows the running job wherever the user has navigated to", () => {
    render(<JobIndicator activeJobs={[job()]} />);

    expect(screen.getByTestId("job-indicator")).toBeInTheDocument();
    expect(screen.getByText("42%")).toBeInTheDocument();
  });

  it("mentions the others when several are running", () => {
    render(<JobIndicator activeJobs={[job({ id: "a" }), job({ id: "b" }), job({ id: "c" })]} />);

    expect(screen.getByText(/\+2 more/)).toBeInTheDocument();
  });
});

describe("JobsPanel cancel", () => {
  it("offers cancel on a job that is still going", () => {
    const onCancel = vi.fn();
    render(<JobsPanel jobs={[job()]} onCancel={onCancel} />);

    screen.getByRole("button", { name: /cancel/i }).click();

    expect(onCancel).toHaveBeenCalledWith(expect.objectContaining({ id: "job-1" }));
  });

  it("does not offer cancel on a job that has already finished", () => {
    render(<JobsPanel jobs={[job({ status: "succeeded" })]} onCancel={vi.fn()} />);

    expect(screen.queryByRole("button", { name: /cancel/i })).not.toBeInTheDocument();
  });

  it("shows a cancelled job as cancelled rather than failed", () => {
    render(<JobsPanel jobs={[job({ status: "cancelled" })]} />);

    expect(screen.getByText("Cancelled")).toBeInTheDocument();
  });

  it("styles a cancelled job distinctly from a failed one", () => {
    // A cancelled job is a deliberate act; it must not wear the same
    // error styling as something that broke.
    const { rerender } = render(<JobsPanel jobs={[job({ status: "cancelled" })]} />);
    const cancelled = screen.getByTestId("job-row").className;

    rerender(<JobsPanel jobs={[job({ status: "failed" })]} />);
    const failed = screen.getByTestId("job-row").className;

    expect(cancelled).not.toEqual(failed);
  });
});

describe("JobsPanel: clearing dead jobs", () => {
  it("offers Dismiss on a finished job", () => {
    render(<JobsPanel jobs={[job({ status: "failed" })]} onDismiss={vi.fn()} />);

    expect(screen.getByRole("button", { name: /dismiss/i })).toBeInTheDocument();
  });

  it("does not offer Dismiss while a job is running", () => {
    // Its worker is still writing to that row and those files, and
    // cancel is the path that actually stops one.
    render(<JobsPanel jobs={[job({ status: "running" })]} onDismiss={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.queryByRole("button", { name: /dismiss/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument();
  });

  it("dismissing asks for that job", () => {
    const onDismiss = vi.fn();
    const failed = job({ status: "failed" });
    render(<JobsPanel jobs={[failed]} onDismiss={onDismiss} />);

    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));

    expect(onDismiss).toHaveBeenCalledWith(failed);
  });

  it("offers to clear all the finished ones, and says how many", () => {
    render(
      <JobsPanel
        jobs={[job({ status: "failed" }), job({ status: "cancelled" }), job({ status: "running" })]}
        onClearFinished={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /clear 2 finished/i })).toBeInTheDocument();
  });

  it("does not offer to clear when everything is still running", () => {
    render(<JobsPanel jobs={[job({ status: "running" })]} onClearFinished={vi.fn()} />);

    expect(screen.queryByRole("button", { name: /clear/i })).not.toBeInTheDocument();
  });
});
