import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, setAuthToken } from "../src/api";
import App from "../src/App";

const admin = {
  id: "u-admin",
  username: "harsh",
  display_name: "Harsh",
  role: "admin",
  is_active: true,
  created_at: "2026-10-03T00:00:00",
  last_login_at: null,
};
const ravi = { ...admin, id: "u-ravi", username: "ravi", display_name: "Ravi", role: "user" };

const project = {
  id: "p-1",
  name: "Gantry 7",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "anpr-v1",
  workspace_path: "/workspace/p-1",
};

const sourceQueue = {
  source_id: "s-1",
  path_or_uri: "rtsp://admin:pw@10.0.0.5:554/Streaming/channels/1",
  type: "rtsp",
  total: 3,
  pending: 2,
  labeled: 1,
  rejected: 0,
  skipped: 0,
};

function task(overrides: Record<string, unknown> = {}) {
  return {
    id: "t-1",
    project_id: "p-1",
    project_name: "Gantry 7",
    source_id: "s-1",
    source_type: "rtsp",
    source_path_or_uri: sourceQueue.path_or_uri,
    assignee: { id: "u-ravi", username: "ravi", display_name: "Ravi" },
    status: "assigned",
    review_note: null,
    progress: { pending: 2, labeled: 1, rejected: 0, skipped: 0, total: 3 },
    created_at: "2026-10-03T00:00:00",
    started_at: null,
    submitted_at: null,
    completed_at: null,
    ...overrides,
  };
}

const frames = [0, 1, 2].map((i) => ({
  id: `f-${i}`,
  source_id: "s-1",
  frame_index: i * 10,
  timestamp_ms: i * 1000,
  width: 64,
  height: 48,
  status: i === 0 ? "labeled" : "pending",
  selection_reason: null,
}));

function signIn(user: typeof admin) {
  setAuthToken("tok");
  vi.spyOn(api, "getMe").mockResolvedValue(user as never);
}

async function openProject() {
  fireEvent.click(await screen.findByRole("button", { name: /gantry 7 open/i }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  setAuthToken(null);
  vi.spyOn(api, "getSchemaState").mockResolvedValue({ current: "x", head: "x", up_to_date: true, pending: [] });
  vi.spyOn(api, "listProjects").mockResolvedValue([project as never]);
  vi.spyOn(api, "getQueueBySource").mockResolvedValue([sourceQueue as never]);
  vi.spyOn(api, "listFrames").mockResolvedValue(frames as never);
  vi.spyOn(api, "getQueueProgress").mockResolvedValue({ pending: 2, labeled: 1, rejected: 0, skipped: 0, total: 3 });
});

describe("a user's tasks", () => {
  it("shows only My tasks, and asks for nothing an admin would", async () => {
    signIn(ravi);
    vi.spyOn(api, "listTasks").mockResolvedValue([task() as never]);
    const jobs = vi.spyOn(api, "listJobs");
    const tracks = vi.spyOn(api, "listTracks");
    render(<App />);
    await openProject();

    const nav = await screen.findByRole("navigation", { name: /modules/i });
    expect(within(nav).getAllByRole("button").map((b) => b.textContent)).toEqual(["My tasks"]);
    expect(await screen.findByText("10.0.0.5 · ch 1")).toBeInTheDocument();
    expect(jobs).not.toHaveBeenCalled();
    expect(tracks).not.toHaveBeenCalled();
  });

  it("opens a task's frames, starting it", async () => {
    signIn(ravi);
    vi.spyOn(api, "listTasks").mockResolvedValue([task() as never]);
    const start = vi.spyOn(api, "startTask").mockResolvedValue(task({ status: "in_progress" }) as never);
    vi.spyOn(api, "getTask").mockResolvedValue(task({ status: "in_progress" }) as never);
    render(<App />);
    await openProject();

    fireEvent.click(await screen.findByRole("button", { name: /^start$/i }));

    expect(await screen.findByRole("button", { name: /label frame 10/i })).toBeInTheDocument();
    expect(start).toHaveBeenCalledWith("t-1");
    await waitFor(() => expect(api.listFrames).toHaveBeenCalledWith("p-1", undefined, "s-1"));
  });

  it("will not submit while frames are pending", async () => {
    signIn(ravi);
    vi.spyOn(api, "listTasks").mockResolvedValue([task({ status: "in_progress" }) as never]);
    render(<App />);
    await openProject();

    expect(await screen.findByRole("button", { name: /submit for review/i })).toBeDisabled();
  });

  it("submits once every frame is done", async () => {
    signIn(ravi);
    const done = task({ status: "in_progress", progress: { pending: 0, labeled: 3, rejected: 0, skipped: 0, total: 3 } });
    const list = vi.spyOn(api, "listTasks").mockResolvedValue([done as never]);
    const submit = vi.spyOn(api, "submitTask").mockResolvedValue(task({ status: "in_review" }) as never);
    render(<App />);
    await openProject();

    list.mockResolvedValue([task({ status: "in_review" }) as never]);
    fireEvent.click(await screen.findByRole("button", { name: /submit for review/i }));

    await waitFor(() => expect(submit).toHaveBeenCalledWith("t-1"));
    expect(await screen.findByText(/waiting for an admin/i)).toBeInTheDocument();
  });

  it("shows why a task was sent back", async () => {
    signIn(ravi);
    vi.spyOn(api, "listTasks").mockResolvedValue([
      task({ status: "in_progress", review_note: "Plates on the night frames are missing" }) as never,
    ]);
    render(<App />);
    await openProject();

    expect(await screen.findByText(/plates on the night frames are missing/i)).toBeInTheDocument();
  });
});

describe("an admin's tasks", () => {
  beforeEach(() => {
    signIn(admin);
    vi.spyOn(api, "listSources").mockResolvedValue([]);
    vi.spyOn(api, "listModels").mockResolvedValue([]);
    vi.spyOn(api, "listJobs").mockResolvedValue([]);
    vi.spyOn(api, "listTracks").mockResolvedValue([]);
    vi.spyOn(api, "listUsers").mockResolvedValue([admin as never, ravi as never]);
  });

  async function openTasks() {
    render(<App />);
    await openProject();
    const nav = await screen.findByRole("navigation", { name: /modules/i });
    fireEvent.click(within(nav).getByRole("button", { name: /tasks/i }));
  }

  it("assigns a source to someone", async () => {
    const list = vi.spyOn(api, "listTasks").mockResolvedValue([]);
    const create = vi.spyOn(api, "createTask").mockResolvedValue(task() as never);
    await openTasks();

    fireEvent.change(await screen.findByLabelText(/source to assign/i), { target: { value: "s-1" } });
    fireEvent.change(screen.getByLabelText(/^assign to$/i), { target: { value: "u-ravi" } });
    list.mockResolvedValue([task() as never]);
    fireEvent.click(screen.getByRole("button", { name: /^assign$/i }));

    await waitFor(() => expect(create).toHaveBeenCalledWith("p-1", "s-1", "u-ravi"));
    expect(await screen.findByText(/is now ravi's task/i)).toBeInTheDocument();
  });

  it("does not offer a source that already has a task", async () => {
    vi.spyOn(api, "listTasks").mockResolvedValue([task() as never]);
    await openTasks();

    const select = await screen.findByLabelText(/source to assign/i);
    await screen.findByText(/1\/3 done/);
    expect(within(select).queryByRole("option", { name: /10\.0\.0\.5/ })).not.toBeInTheDocument();
  });

  it("accepts a task in review", async () => {
    const list = vi.spyOn(api, "listTasks").mockResolvedValue([task({ status: "in_review" }) as never]);
    const accept = vi.spyOn(api, "acceptTask").mockResolvedValue(task({ status: "done" }) as never);
    await openTasks();

    list.mockResolvedValue([task({ status: "done" }) as never]);
    fireEvent.click(await screen.findByRole("button", { name: /^accept$/i }));

    await waitFor(() => expect(accept).toHaveBeenCalledWith("t-1"));
    expect(await screen.findByRole("button", { name: /reopen/i })).toBeInTheDocument();
  });

  it("asks why before sending a task back", async () => {
    vi.spyOn(api, "listTasks").mockResolvedValue([task({ status: "in_review" }) as never]);
    const reject = vi.spyOn(api, "rejectTask").mockResolvedValue(task({ status: "in_progress" }) as never);
    await openTasks();

    fireEvent.click(await screen.findByRole("button", { name: /^reject$/i }));
    const send = screen.getByRole("button", { name: /send back/i });
    expect(send).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/what needs fixing/i), { target: { value: "Missed plates" } });
    fireEvent.click(send);
    await waitFor(() => expect(reject).toHaveBeenCalledWith("t-1", "Missed plates"));
  });
});
