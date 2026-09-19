import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api";
import ProjectPicker from "../src/components/ProjectPicker";
import type { Project, ProjectContents } from "../src/types";

const projects: Project[] = [
  {
    id: "p-1",
    name: "Gantry North",
    created_at: "2026-09-19T00:00:00+00:00",
    class_schema_version: "atcc-v1",
    workspace_path: "/workspace/p-1",
  },
  {
    id: "p-2",
    name: "Gantry South",
    created_at: "2026-09-18T00:00:00+00:00",
    class_schema_version: "atcc-v1",
    workspace_path: "/workspace/p-2",
  },
];

function contents(overrides: Partial<ProjectContents> = {}): ProjectContents {
  return {
    sources: 2,
    frames: 4029,
    tracks: 328,
    labels: 28,
    dataset_versions: 6,
    workspace_bytes: 1_200_000_000,
    running_jobs: 0,
    workspace_removed: false,
    ...overrides,
  };
}

function openDeleteFor(name: string) {
  const row = screen.getByText(name).closest("li");
  fireEvent.click(row!.querySelector("[data-testid='delete-project']")!);
}

describe("ProjectPicker: deleting a project", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listProjects").mockResolvedValue(projects);
    vi.spyOn(api, "getProjectContents").mockResolvedValue(contents());
  });

  it("offers a delete control per project", async () => {
    render(<ProjectPicker onSelect={vi.fn()} />);

    await screen.findByText("Gantry North");
    expect(screen.getAllByTestId("delete-project")).toHaveLength(2);
  });

  it("says what deleting would destroy before asking", async () => {
    // A confirmation that cannot say what is about to go is not a
    // confirmation.
    render(<ProjectPicker onSelect={vi.fn()} />);
    await screen.findByText("Gantry North");

    openDeleteFor("Gantry North");

    const summary = await screen.findByTestId("delete-summary");
    expect(summary.textContent).toContain("28");
    expect(summary.textContent).toContain("328");
    expect(summary.textContent).toContain("6");
    expect(summary.textContent).toMatch(/1\.1 GB|1\.2 GB/);
  });

  it("will not delete until the name is typed exactly", async () => {
    render(<ProjectPicker onSelect={vi.fn()} />);
    await screen.findByText("Gantry North");
    openDeleteFor("Gantry North");

    const confirm = await screen.findByRole("button", { name: /delete this project/i });
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/type the project name/i), { target: { value: "gantry north" } });
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/type the project name/i), { target: { value: "Gantry North" } });
    expect(confirm).not.toBeDisabled();
  });

  it("deletes the project and drops it from the list", async () => {
    const remove = vi.spyOn(api, "deleteProject").mockResolvedValue(contents({ workspace_removed: true }));
    render(<ProjectPicker onSelect={vi.fn()} />);
    await screen.findByText("Gantry North");
    openDeleteFor("Gantry North");

    fireEvent.change(await screen.findByLabelText(/type the project name/i), { target: { value: "Gantry North" } });
    fireEvent.click(screen.getByRole("button", { name: /delete this project/i }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith("p-1", "Gantry North"));
    await waitFor(() => expect(screen.queryByText("Gantry North")).not.toBeInTheDocument());
    expect(screen.getByText("Gantry South")).toBeInTheDocument();
  });

  it("refuses while work is running, and says so", async () => {
    // A detached worker is still writing to those rows and that
    // directory.
    vi.spyOn(api, "getProjectContents").mockResolvedValue(contents({ running_jobs: 2 }));
    render(<ProjectPicker onSelect={vi.fn()} />);
    await screen.findByText("Gantry North");

    openDeleteFor("Gantry North");

    expect(await screen.findByText(/2 job\(s\) still running/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/type the project name/i), { target: { value: "Gantry North" } });
    expect(screen.getByRole("button", { name: /delete this project/i })).toBeDisabled();
  });

  it("shows the server's reason when it refuses", async () => {
    vi.spyOn(api, "deleteProject").mockRejectedValue(
      new ApiError(409, "project_busy", "1 job(s) are still running for this project."),
    );
    render(<ProjectPicker onSelect={vi.fn()} />);
    await screen.findByText("Gantry North");
    openDeleteFor("Gantry North");

    fireEvent.change(await screen.findByLabelText(/type the project name/i), { target: { value: "Gantry North" } });
    fireEvent.click(screen.getByRole("button", { name: /delete this project/i }));

    expect(await screen.findByText(/still running for this project/i)).toBeInTheDocument();
    // The row survives. Checked by its own control rather than by text,
    // because the confirmation panel names the project too.
    expect(screen.getByRole("button", { name: "Delete Gantry North" })).toBeInTheDocument();
  });

  it("backing out leaves the project alone", async () => {
    const remove = vi.spyOn(api, "deleteProject").mockResolvedValue(contents());
    render(<ProjectPicker onSelect={vi.fn()} />);
    await screen.findByText("Gantry North");
    openDeleteFor("Gantry North");

    fireEvent.click(await screen.findByRole("button", { name: /^cancel$/i }));

    expect(remove).not.toHaveBeenCalled();
    expect(screen.getByText("Gantry North")).toBeInTheDocument();
    expect(screen.queryByTestId("delete-summary")).not.toBeInTheDocument();
  });

  it("asking to delete does not open the project", async () => {
    // The delete control sits inside the row that opens it, so a click
    // that reaches both would open the project it just destroyed.
    const onSelect = vi.fn();
    render(<ProjectPicker onSelect={onSelect} />);
    await screen.findByText("Gantry North");

    openDeleteFor("Gantry North");

    await screen.findByTestId("delete-summary");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("says when the workspace could not be removed", async () => {
    // Rows gone, files left. The user needs to know there is a
    // directory to clear by hand.
    vi.spyOn(api, "deleteProject").mockResolvedValue(contents({ workspace_removed: false }));
    render(<ProjectPicker onSelect={vi.fn()} />);
    await screen.findByText("Gantry North");
    openDeleteFor("Gantry North");

    fireEvent.change(await screen.findByLabelText(/type the project name/i), { target: { value: "Gantry North" } });
    fireEvent.click(screen.getByRole("button", { name: /delete this project/i }));

    expect(await screen.findByText(/files were left on disk/i)).toBeInTheDocument();
  });
});

describe("ProjectPicker: what the confirmation says", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listProjects").mockResolvedValue(projects);
  });

  it("lists only what is actually there", async () => {
    // A row of zeros makes the reader hunt for the one number that
    // matters.
    vi.spyOn(api, "getProjectContents").mockResolvedValue(
      contents({ labels: 0, frames: 0, dataset_versions: 0, tracks: 3, sources: 1, workspace_bytes: 51_000_000 }),
    );
    render(<ProjectPicker onSelect={vi.fn()} />);
    await screen.findByText("Gantry North");

    openDeleteFor("Gantry North");

    const summary = await screen.findByTestId("delete-summary");
    expect(summary.textContent).toContain("3 tracks");
    expect(summary.textContent).toContain("1 source");
    expect(summary.textContent).not.toContain("0 ");
    expect(summary.textContent).toContain("cannot be undone");
  });

  it("says so when there is nothing in the project", async () => {
    vi.spyOn(api, "getProjectContents").mockResolvedValue(
      contents({ sources: 0, frames: 0, tracks: 0, labels: 0, dataset_versions: 0, workspace_bytes: 0 }),
    );
    render(<ProjectPicker onSelect={vi.fn()} />);
    await screen.findByText("Gantry North");

    openDeleteFor("Gantry North");

    expect((await screen.findByTestId("delete-summary")).textContent).toMatch(/nothing in this project/i);
  });

  it("uses singular and plural correctly", async () => {
    vi.spyOn(api, "getProjectContents").mockResolvedValue(
      contents({ labels: 1, tracks: 2, frames: 0, sources: 1, dataset_versions: 0, workspace_bytes: 0 }),
    );
    render(<ProjectPicker onSelect={vi.fn()} />);
    await screen.findByText("Gantry North");

    openDeleteFor("Gantry North");

    const summary = await screen.findByTestId("delete-summary");
    expect(summary.textContent).toContain("1 label");
    expect(summary.textContent).toContain("2 tracks");
    expect(summary.textContent).not.toContain("1 labels");
  });
});
