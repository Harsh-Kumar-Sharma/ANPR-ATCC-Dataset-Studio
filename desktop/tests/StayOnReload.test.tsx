import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, setAuthToken } from "../src/api";
import App from "../src/App";
import { placeToHash, readPlace } from "../src/location";

const admin = {
  id: "u-admin",
  username: "harsh",
  display_name: "Harsh",
  role: "admin",
  is_active: true,
  created_at: "2026-10-03T00:00:00",
  last_login_at: null,
};

const project = {
  id: "p-1",
  name: "Gantry 7",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "anpr-v1",
  workspace_path: "/workspace/p-1",
};

const frame = {
  id: "f-9",
  source_id: "s-1",
  frame_index: 440,
  timestamp_ms: 0,
  width: 64,
  height: 48,
  status: "pending",
  selection_reason: null,
};

describe("the place in the URL", () => {
  it("round-trips", () => {
    const place = { projectId: "p-1", tab: "label", frameId: "f-9" };
    expect(readPlace(placeToHash(place))).toEqual(place);
  });

  it("is nothing without a project", () => {
    expect(readPlace("")).toBeNull();
    expect(readPlace("#/elsewhere")).toBeNull();
  });
});

describe("reloading", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    window.history.replaceState(null, "", "/");
    setAuthToken("tok");
    vi.spyOn(api, "getMe").mockResolvedValue(admin as never);
    vi.spyOn(api, "getSchemaState").mockResolvedValue({ current: "x", head: "x", up_to_date: true, pending: [] });
    vi.spyOn(api, "listProjects").mockResolvedValue([project as never]);
    vi.spyOn(api, "getProject").mockResolvedValue(project as never);
    vi.spyOn(api, "listSources").mockResolvedValue([]);
    vi.spyOn(api, "listModels").mockResolvedValue([]);
    vi.spyOn(api, "listJobs").mockResolvedValue([]);
    vi.spyOn(api, "listTracks").mockResolvedValue([]);
    vi.spyOn(api, "getQueueBySource").mockResolvedValue([]);
    vi.spyOn(api, "listFrames").mockResolvedValue([frame as never]);
    vi.spyOn(api, "getQueueProgress").mockResolvedValue({ pending: 1, labeled: 0, rejected: 0, skipped: 0, total: 1 });
  });

  it("records the module you are in", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /gantry 7 open/i }));
    const nav = await screen.findByRole("navigation", { name: /modules/i });
    fireEvent.click(within(nav).getByRole("button", { name: /label/i }));

    await waitFor(() => expect(window.location.hash).toBe("#/p/p-1/label"));
  });

  it("comes back to the same module rather than the project list", async () => {
    window.history.replaceState(null, "", "/#/p/p-1/label");
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Label" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /gantry 7 open/i })).not.toBeInTheDocument();
  });

  it("comes back to the frame that was open", async () => {
    vi.spyOn(api, "getFrame").mockResolvedValue(frame as never);
    vi.spyOn(api, "getFrameAnnotations").mockResolvedValue([]);
    vi.spyOn(api, "getFrameSuggestions").mockResolvedValue([]);
    vi.spyOn(api, "getClassSchema").mockResolvedValue([]);
    vi.spyOn(api, "listAttributeDefinitions").mockResolvedValue([]);
    vi.spyOn(api, "getFramePlateReadings").mockResolvedValue([]);
    window.history.replaceState(null, "", "/#/p/p-1/label/frame/f-9");
    render(<App />);

    expect(await screen.findByText(/frame 440 · 0 boxes/)).toBeInTheDocument();
  });

  it("falls back to the project list when the project has gone", async () => {
    const { ApiError } = await import("../src/api");
    vi.mocked(api.getProject).mockRejectedValue(new ApiError(404, "not_found", "Project not found"));
    window.history.replaceState(null, "", "/#/p/gone/label");
    render(<App />);

    expect(await screen.findByRole("button", { name: /gantry 7 open/i })).toBeInTheDocument();
    await waitFor(() => expect(window.location.hash).toBe(""));
  });

  it("going back to Projects forgets the place", async () => {
    window.history.replaceState(null, "", "/#/p/p-1/label");
    render(<App />);
    await screen.findByRole("heading", { name: "Label" });

    fireEvent.click(screen.getByRole("button", { name: /^projects$/i }));
    await waitFor(() => expect(window.location.hash).toBe(""));
  });
});
