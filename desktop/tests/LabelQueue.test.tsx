import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import LabelQueue from "../src/components/LabelQueue";
import type { Frame, Project, QueueProgress, SourceQueue } from "../src/types";

const project: Project = {
  id: "p-1",
  name: "Test",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "anpr-v1",
  workspace_path: "/workspace/p-1",
};

function frame(index: number, status: Frame["status"] = "pending"): Frame {
  return {
    id: `f-${index}`,
    source_id: "s-1",
    frame_index: index,
    timestamp_ms: index * 100,
    width: 640,
    height: 480,
    status,
    selection_reason: null,
  };
}

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

const progress = (over: Partial<QueueProgress> = {}): QueueProgress => ({
  pending: 3,
  labeled: 0,
  rejected: 0,
  skipped: 0,
  total: 3,
  ...over,
});

function renderQueue(props: Partial<React.ComponentProps<typeof LabelQueue>> = {}) {
  return render(
    <LabelQueue project={project} selectedFrameId={null} onSelect={vi.fn()} dirty={false} {...props} />,
  );
}

describe("LabelQueue", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listFrames").mockResolvedValue([frame(0), frame(1), frame(2)]);
    vi.spyOn(api, "getQueueProgress").mockResolvedValue(progress());
    vi.spyOn(api, "getQueueBySource").mockResolvedValue([sourceQueue()]);
  });

  it("says so plainly when there is nothing to label", async () => {
    vi.spyOn(api, "listFrames").mockResolvedValue([]);
    vi.spyOn(api, "getQueueProgress").mockResolvedValue(progress({ pending: 0, total: 0 }));

    renderQueue();

    expect(await screen.findByText(/no frames yet/i)).toBeInTheDocument();
  });

  it("shows how far through the queue the labelling has got", async () => {
    vi.spyOn(api, "getQueueProgress").mockResolvedValue(progress({ pending: 5, labeled: 3, rejected: 2, total: 10 }));

    renderQueue();

    const summary = await screen.findByTestId("queue-progress");
    expect(summary).toHaveTextContent(/3 labelled/i);
    expect(summary).toHaveTextContent(/2 set aside/i);
    expect(summary).toHaveTextContent(/5 left/i);
  });

  it("opens the frame the user clicks", async () => {
    const onSelect = vi.fn();
    renderQueue({ onSelect });

    fireEvent.click(await screen.findByRole("button", { name: /frame 1/i }));

    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "f-1" }));
  });

  it("next and previous walk the queue", async () => {
    const onSelect = vi.fn();
    renderQueue({ selectedFrameId: "f-1", onSelect });
    await screen.findByRole("button", { name: /frame 1/i });

    fireEvent.click(screen.getByRole("button", { name: /next frame/i }));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "f-2" }));

    fireEvent.click(screen.getByRole("button", { name: /previous frame/i }));
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: "f-0" }));
  });

  it("stops at the ends rather than wrapping", async () => {
    const onSelect = vi.fn();
    renderQueue({ selectedFrameId: "f-0", onSelect });
    await screen.findByRole("button", { name: /frame 0/i });

    expect(screen.getByRole("button", { name: /previous frame/i })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /next frame/i }));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "f-1" }));
  });

  it("disables next on the last frame", async () => {
    renderQueue({ selectedFrameId: "f-2" });
    await screen.findByRole("button", { name: /frame 2/i });

    expect(screen.getByRole("button", { name: /next frame/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /previous frame/i })).toBeEnabled();
  });

  it("offers the set-aside frames only when there are some", async () => {
    renderQueue();
    await screen.findByRole("button", { name: /frame 0/i });
    expect(screen.queryByLabelText(/show set-aside/i)).not.toBeInTheDocument();
  });

  it("can show what was set aside and put a frame back", async () => {
    // The claim that skipping is reversible needs somewhere to reverse it.
    vi.spyOn(api, "getQueueProgress").mockResolvedValue(progress({ pending: 2, rejected: 1, total: 3 }));
    // Answer by status rather than swapping the mock mid-flight, which
    // races the refresh the click kicks off.
    const listFrames = vi
      .spyOn(api, "listFrames")
      .mockImplementation(async (_projectId, status) =>
        status === "rejected" ? [frame(5, "rejected")] : status === "skipped" ? [] : [frame(0), frame(1)],
      );
    const setStatus = vi.spyOn(api, "setFrameStatus").mockResolvedValue(frame(5));
    renderQueue();

    fireEvent.click(await screen.findByLabelText(/show set-aside/i));

    // Both set-aside statuses are fetched, so check both rather than
    // whichever happened to resolve last.
    await waitFor(() => expect(listFrames).toHaveBeenCalledWith("p-1", "rejected", null));
    expect(listFrames).toHaveBeenCalledWith("p-1", "skipped", null);

    fireEvent.click(await screen.findByRole("button", { name: /put frame 5 back/i }));

    await waitFor(() => expect(setStatus).toHaveBeenCalledWith("f-5", "pending"));
  });

  it("shows why a frame is in the queue, when selection has said", async () => {
    // A bad queue is only fixable if each frame can say how it got in.
    vi.spyOn(api, "listFrames").mockResolvedValue([
      { ...frame(0), selection_reason: "quality 0.57, 2 vehicle(s), brightness 0.40" },
      frame(1),
    ]);

    renderQueue();

    expect(await screen.findByText(/quality 0\.57, 2 vehicle\(s\)/)).toBeInTheDocument();
  });

  it("marks which frames are done", async () => {
    vi.spyOn(api, "listFrames").mockResolvedValue([frame(0, "labeled"), frame(1)]);

    renderQueue();

    await waitFor(() => expect(screen.getByRole("button", { name: /frame 0/i })).toHaveTextContent(/labeled/i));
    expect(screen.getByRole("button", { name: /frame 1/i })).toHaveTextContent(/pending/i);
  });

  it("remembers where the user was and offers it back", async () => {
    window.localStorage.setItem("anpr:last-frame:p-1", "f-2");
    const onSelect = vi.fn();

    renderQueue({ onSelect });

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "f-2" })));
  });

  it("does not resume onto a frame that is no longer in the queue", async () => {
    window.localStorage.setItem("anpr:last-frame:p-1", "f-99");
    const onSelect = vi.fn();

    renderQueue({ onSelect });

    await screen.findByRole("button", { name: /frame 0/i });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("does not hijack the view when a frame is already open", async () => {
    window.localStorage.setItem("anpr:last-frame:p-1", "f-2");
    const onSelect = vi.fn();

    renderQueue({ selectedFrameId: "f-0", onSelect });

    await screen.findByRole("button", { name: /frame 0/i });
    expect(onSelect).not.toHaveBeenCalled();
  });
});

describe("LabelQueue: unsaved work", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listFrames").mockResolvedValue([frame(0), frame(1), frame(2)]);
    vi.spyOn(api, "getQueueProgress").mockResolvedValue(progress());
    vi.spyOn(api, "getQueueBySource").mockResolvedValue([sourceQueue()]);
  });

  it("will not walk away from unsaved boxes without saying so", async () => {
    const onSelect = vi.fn();
    renderQueue({ selectedFrameId: "f-0", dirty: true, onSelect });
    await screen.findByRole("button", { name: /frame 1/i });

    fireEvent.click(screen.getByRole("button", { name: /frame 1/i }));

    expect(onSelect).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent(/unsaved/i);
  });

  it("lets the user discard and go anyway", async () => {
    const onSelect = vi.fn();
    renderQueue({ selectedFrameId: "f-0", dirty: true, onSelect });
    fireEvent.click(await screen.findByRole("button", { name: /frame 1/i }));

    fireEvent.click(screen.getByRole("button", { name: /discard and open/i }));

    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "f-1" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("lets the user stay and keep working", async () => {
    const onSelect = vi.fn();
    renderQueue({ selectedFrameId: "f-0", dirty: true, onSelect });
    fireEvent.click(await screen.findByRole("button", { name: /frame 1/i }));

    fireEvent.click(screen.getByRole("button", { name: /stay/i }));

    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not prompt when reopening the frame already on screen", async () => {
    const onSelect = vi.fn();
    renderQueue({ selectedFrameId: "f-0", dirty: true, onSelect });

    fireEvent.click(await screen.findByRole("button", { name: /frame 0/i }));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("guards next and previous too", async () => {
    const onSelect = vi.fn();
    renderQueue({ selectedFrameId: "f-0", dirty: true, onSelect });
    await screen.findByRole("button", { name: /frame 0/i });

    fireEvent.click(screen.getByRole("button", { name: /next frame/i }));

    expect(onSelect).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent(/unsaved/i);
  });
});

describe("LabelQueue, choosing a source", () => {
  const two = [
    sourceQueue({ source_id: "s-1", path_or_uri: "C:/clips/gantry_north.mp4", pending: 3, total: 3 }),
    sourceQueue({ source_id: "s-2", path_or_uri: "C:/clips/toll_west.mp4", pending: 1, total: 4, labeled: 3 }),
  ];

  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "listFrames").mockResolvedValue([frame(0), frame(1), frame(2)]);
    vi.spyOn(api, "getQueueProgress").mockResolvedValue(progress());
    vi.spyOn(api, "getQueueBySource").mockResolvedValue(two);
  });

  it("offers no choice when there is only one source", async () => {
    vi.spyOn(api, "getQueueBySource").mockResolvedValue([sourceQueue()]);

    renderQueue();
    await screen.findByRole("button", { name: /frame 0/i });

    expect(screen.queryByLabelText(/source to label/i)).not.toBeInTheDocument();
  });

  it("names each source and says how much of it is left", async () => {
    renderQueue();

    const picker = await screen.findByLabelText(/source to label/i);
    expect(picker).toHaveTextContent(/gantry_north\.mp4/);
    expect(picker).toHaveTextContent(/toll_west\.mp4/);
    expect(picker).toHaveTextContent(/1 left of 4/);
  });

  it("narrows both the list and the progress line to the chosen source", async () => {
    renderQueue();
    const picker = await screen.findByLabelText(/source to label/i);

    fireEvent.change(picker, { target: { value: "s-2" } });

    await waitFor(() => {
      expect(api.listFrames).toHaveBeenCalledWith(project.id, undefined, "s-2");
      expect(api.getQueueProgress).toHaveBeenCalledWith(project.id, "s-2");
    });
  });

  it("remembers the choice for next time, per project", async () => {
    const first = renderQueue();
    fireEvent.change(await screen.findByLabelText(/source to label/i), { target: { value: "s-2" } });
    await waitFor(() => expect(api.listFrames).toHaveBeenCalledWith(project.id, undefined, "s-2"));
    first.unmount();

    vi.mocked(api.listFrames).mockClear();
    renderQueue();

    await waitFor(() => expect(api.listFrames).toHaveBeenCalledWith(project.id, undefined, "s-2"));
  });

  it("falls back to all sources when the remembered one is gone", async () => {
    window.localStorage.setItem(`anpr:last-source:${project.id}`, "s-deleted");

    renderQueue();

    // No request is made for the source that no longer exists: it would
    // answer 404 and strand the tab on an error.
    await waitFor(() => expect(api.listFrames).toHaveBeenCalledWith(project.id, undefined, null));
    expect(api.listFrames).not.toHaveBeenCalledWith(project.id, undefined, "s-deleted");
  });

  it("uses the source the rest of the app picked over the remembered one", async () => {
    window.localStorage.setItem(`anpr:last-source:${project.id}`, "s-1");

    renderQueue({ sourceId: "s-2" });

    await waitFor(() => expect(api.listFrames).toHaveBeenCalledWith(project.id, undefined, "s-2"));
  });

  it("tells the rest of the app when the user picks a different source", async () => {
    const onSourceChange = vi.fn();
    renderQueue({ onSourceChange });

    fireEvent.change(await screen.findByLabelText(/source to label/i), { target: { value: "s-2" } });

    expect(onSourceChange).toHaveBeenCalledWith("s-2");
  });
});
