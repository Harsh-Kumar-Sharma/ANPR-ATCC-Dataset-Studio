/**
 * Every frame at once, as pictures.
 *
 * The queue is a list of "frame 0, frame 7, frame 14", which says
 * nothing about which of two hundred frames has a vehicle in it.
 * Finding the four worth labelling meant opening them one at a time.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import FrameGrid from "../src/components/FrameGrid";
import type { Frame } from "../src/types";

function frame(index: number, status: Frame["status"] = "pending"): Frame {
  return {
    id: `f-${index}`,
    source_id: "s-1",
    frame_index: index,
    timestamp_ms: index * 100,
    width: 1920,
    height: 1080,
    status,
    selection_reason: null,
  };
}

const many = [frame(0), frame(7), frame(14, "labeled"), frame(21)];

describe("FrameGrid", () => {
  it("shows a picture of every frame in the queue", () => {
    const { container } = render(
      <FrameGrid frames={many} selectedFrameId={null} onOpen={vi.fn()} onClose={vi.fn()} />,
    );

    // Queried by tag: the thumbnails carry an empty alt on purpose,
    // which makes them decorative. The button around each one is what
    // names the frame.
    expect(container.querySelectorAll("img")).toHaveLength(many.length);
    expect(screen.getByText(/all frames \(4\)/i)).toBeInTheDocument();
  });

  it("asks for thumbnails, not full frames", () => {
    // Two hundred full-size images is tens of megabytes and, for a
    // video source, two hundred decodes written to disk.
    const { container } = render(
      <FrameGrid frames={many} selectedFrameId={null} onOpen={vi.fn()} onClose={vi.fn()} />,
    );

    for (const image of container.querySelectorAll("img")) {
      expect(image).toHaveAttribute("src", expect.stringContaining("/thumbnail"));
    }
  });

  it("loads them lazily, so opening the grid does not fetch all two hundred", () => {
    const { container } = render(
      <FrameGrid frames={many} selectedFrameId={null} onOpen={vi.fn()} onClose={vi.fn()} />,
    );

    for (const image of container.querySelectorAll("img")) {
      expect(image).toHaveAttribute("loading", "lazy");
    }
  });

  it("opens the one that was clicked, for labelling", () => {
    const onOpen = vi.fn();
    render(<FrameGrid frames={many} selectedFrameId={null} onOpen={onOpen} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /label frame 14/i }));

    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: "f-14" }));
  });

  it("says which ones are already labelled, so they can be skipped", () => {
    render(<FrameGrid frames={many} selectedFrameId={null} onOpen={vi.fn()} onClose={vi.fn()} />);

    expect(screen.getByText("labeled")).toBeInTheDocument();
    expect(screen.getAllByText("pending")).toHaveLength(3);
  });

  it("marks the frame that is open", () => {
    render(<FrameGrid frames={many} selectedFrameId="f-7" onOpen={vi.fn()} onClose={vi.fn()} />);

    expect(screen.getByRole("button", { name: /label frame 7/i })).toHaveClass("selected");
    expect(screen.getByRole("button", { name: /label frame 0/i })).not.toHaveClass("selected");
  });

  it("can be closed again", () => {
    const onClose = vi.fn();
    render(<FrameGrid frames={many} selectedFrameId={null} onOpen={vi.fn()} onClose={onClose} />);

    fireEvent.click(screen.getByRole("button", { name: /back to the frame being labelled/i }));

    expect(onClose).toHaveBeenCalled();
  });

  it("says so plainly when the queue is empty", () => {
    const { container } = render(
      <FrameGrid frames={[]} selectedFrameId={null} onOpen={vi.fn()} onClose={vi.fn()} />,
    );

    expect(screen.getByText(/nothing in the queue/i)).toBeInTheDocument();
    expect(container.querySelectorAll("img")).toHaveLength(0);
  });
});
