/**
 * Moving between frames without leaving the image.
 *
 * Navigation lived only in the sidebar, so labelling meant moving the
 * eye and the mouse away from the work after every frame.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import FrameNav from "../src/components/FrameNav";
import LabelQueue from "../src/components/LabelQueue";
import { useFrameQueue } from "../src/useFrameQueue";
import type { Frame, Project, QueueProgress, SourceQueue } from "../src/types";

const project: Project = {
  id: "p-1",
  name: "Test",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "anpr-v1",
  workspace_path: "/workspace/p-1",
};

function frame(index: number, sourceId = "s-1"): Frame {
  return {
    id: `f-${index}`,
    source_id: sourceId,
    frame_index: index,
    timestamp_ms: index * 100,
    width: 640,
    height: 480,
    status: "pending",
    selection_reason: null,
  };
}

const progress: QueueProgress = { pending: 3, labeled: 0, rejected: 0, skipped: 0, total: 3 };

const sourceQueue = (over: Partial<SourceQueue> = {}): SourceQueue => ({
  source_id: "s-1",
  path_or_uri: "C:/clips/gantry_north.mp4",
  type: "video",
  total: 3,
  pending: 3,
  labeled: 0,
  rejected: 0,
  skipped: 0,
  ...over,
});

interface HarnessProps {
  startAt?: string | null;
  dirty?: boolean;
  sourceId?: string | null;
  onSelect?: (frame: Frame) => void;
}

/** The sidebar and the nav strip over one queue, which is how they run:
 *  the whole point is that there is only one list. */
function Harness({ startAt = null, dirty = false, sourceId, onSelect }: HarnessProps) {
  const [selected, setSelected] = useState<string | null>(startAt);
  const queue = useFrameQueue({
    project,
    selectedFrameId: selected,
    dirty,
    sourceId,
    onSelect: (f) => {
      setSelected(f.id);
      onSelect?.(f);
    },
  });
  return (
    <>
      <LabelQueue queue={queue} selectedFrameId={selected} project={project} />
      <FrameNav queue={queue} />
    </>
  );
}

describe("FrameNav", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listFrames").mockResolvedValue([frame(0), frame(1), frame(2)]);
    vi.spyOn(api, "getQueueProgress").mockResolvedValue(progress);
    vi.spyOn(api, "getQueueBySource").mockResolvedValue([sourceQueue()]);
  });

  it("says where in the queue this frame is", async () => {
    render(<Harness startAt="f-1" />);

    expect(await screen.findByTestId("frame-position")).toHaveTextContent("2 of 3");
  });

  it("walks forward and back from under the image", async () => {
    const onSelect = vi.fn();
    render(<Harness startAt="f-1" onSelect={onSelect} />);
    await screen.findByTestId("frame-position");

    fireEvent.click(screen.getByRole("button", { name: /next frame in the queue/i }));
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: "f-2" }));

    fireEvent.click(screen.getByRole("button", { name: /previous frame in the queue/i }));
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: "f-1" }));
  });

  it("stops at the ends rather than wrapping silently", async () => {
    render(<Harness startAt="f-0" />);
    await screen.findByTestId("frame-position");

    expect(screen.getByRole("button", { name: /previous frame in the queue/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /next frame in the queue/i })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: /next frame in the queue/i }));
    fireEvent.click(screen.getByRole("button", { name: /next frame in the queue/i }));

    await waitFor(() => expect(screen.getByTestId("frame-position")).toHaveTextContent("3 of 3"));
    expect(screen.getByRole("button", { name: /next frame in the queue/i })).toBeDisabled();
  });

  it("moves on Ctrl and an arrow key, and says so", async () => {
    const onSelect = vi.fn();
    render(<Harness startAt="f-0" onSelect={onSelect} />);
    await screen.findByTestId("frame-position");

    fireEvent.keyDown(window, { key: "ArrowRight", ctrlKey: true });
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: "f-1" }));

    fireEvent.keyDown(window, { key: "ArrowLeft", ctrlKey: true });
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: "f-0" }));

    // Bare arrows belong to the canvas: they nudge a box by a pixel.
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: "f-0" }));
  });

  it("leaves the shortcut on screen rather than hidden in the docs", async () => {
    render(<Harness startAt="f-0" />);

    expect(await screen.findByText(/move between frames/i)).toBeInTheDocument();
  });

  it("asks before walking away from unsaved boxes, under the image", async () => {
    const onSelect = vi.fn();
    render(<Harness startAt="f-0" dirty onSelect={onSelect} />);
    await screen.findByTestId("frame-position");

    fireEvent.click(screen.getByRole("button", { name: /next frame in the queue/i }));

    expect(onSelect).not.toHaveBeenCalled();
    // The question belongs where the user was looking, not in the
    // sidebar they had already stopped using.
    const prompt = screen.getByRole("alert");
    expect(prompt).toHaveClass("frame-nav__prompt");

    fireEvent.click(screen.getByRole("button", { name: /discard and open frame 1/i }));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "f-1" }));
  });

  it("stays put when the answer is to stay", async () => {
    const onSelect = vi.fn();
    render(<Harness startAt="f-0" dirty onSelect={onSelect} />);
    await screen.findByTestId("frame-position");

    fireEvent.click(screen.getByRole("button", { name: /next frame in the queue/i }));
    fireEvent.click(screen.getByRole("button", { name: /stay here/i }));

    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("next means the next frame of the chosen source", async () => {
    vi.spyOn(api, "getQueueBySource").mockResolvedValue([
      sourceQueue(),
      sourceQueue({ source_id: "s-2", path_or_uri: "C:/clips/toll_west.mp4" }),
    ]);
    vi.mocked(api.listFrames).mockResolvedValue([frame(7, "s-2"), frame(8, "s-2")]);
    const onSelect = vi.fn();

    render(<Harness sourceId="s-2" startAt="f-7" onSelect={onSelect} />);
    await waitFor(() => expect(api.listFrames).toHaveBeenCalledWith(project.id, undefined, "s-2"));

    fireEvent.click(screen.getByRole("button", { name: /next frame in the queue/i }));

    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: "f-8" }));
    expect(screen.getByTestId("frame-position")).toHaveTextContent("2 of 2");
  });

  it("stays in step with the sidebar, whichever one moved", async () => {
    render(<Harness startAt="f-0" />);
    await screen.findByTestId("frame-position");

    fireEvent.click(screen.getByRole("button", { name: /^frame 2$/i }));

    await waitFor(() => expect(screen.getByTestId("frame-position")).toHaveTextContent("3 of 3"));
  });
});
