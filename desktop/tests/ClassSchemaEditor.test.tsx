import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api";
import ClassSchemaEditor from "../src/components/ClassSchemaEditor";
import type { ClassDefinition, Project } from "../src/types";

const project: Project = {
  id: "p-1",
  name: "Test",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "anpr-v1",
  workspace_path: "/workspace/p-1",
};

function cls(class_id: number, name: string): ClassDefinition {
  return { id: `row-${class_id}`, class_id, name, display_order: class_id - 1 };
}

describe("ClassSchemaEditor", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listClasses").mockResolvedValue([cls(1, "vehicle"), cls(2, "number_plate")]);
  });

  it("lists the project's classes", async () => {
    render(<ClassSchemaEditor project={project} />);

    expect(await screen.findByText("vehicle")).toBeInTheDocument();
    expect(screen.getByText("number_plate")).toBeInTheDocument();
  });

  it("adds a class", async () => {
    const created = vi.spyOn(api, "createClass").mockResolvedValue(cls(3, "truck"));
    render(<ClassSchemaEditor project={project} />);

    const input = (await screen.findByLabelText(/new class name/i)) as HTMLInputElement;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "truck");
    input.dispatchEvent(new Event("change", { bubbles: true }));

    screen.getByRole("button", { name: /^add$/i }).click();

    await waitFor(() => expect(created).toHaveBeenCalledWith("p-1", "truck"));
  });

  it("says why a duplicate name was refused instead of failing silently", async () => {
    // The whole point of surfacing the backend's message: a name clash is
    // an ordinary mistake and the user needs to know which name clashed.
    vi.spyOn(api, "createClass").mockRejectedValue(
      new ApiError(409, "duplicate_class_name", "This project already has a class called 'vehicle'."),
    );
    render(<ClassSchemaEditor project={project} />);

    const input = (await screen.findByLabelText(/new class name/i)) as HTMLInputElement;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "vehicle");
    input.dispatchEvent(new Event("change", { bubbles: true }));
    screen.getByRole("button", { name: /^add$/i }).click();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/already has a class called 'vehicle'/i);
  });

  it("renames a class and tells the rest of the app to reload", async () => {
    const renamed = vi.spyOn(api, "renameClass").mockResolvedValue(cls(2, "plate"));
    const onClassesChanged = vi.fn();
    render(<ClassSchemaEditor project={project} onClassesChanged={onClassesChanged} />);

    (await screen.findByRole("button", { name: /rename number_plate/i })).click();

    const input = (await screen.findByLabelText(/new name for number_plate/i)) as HTMLInputElement;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "plate");
    input.dispatchEvent(new Event("change", { bubbles: true }));
    screen.getByRole("button", { name: /^save$/i }).click();

    await waitFor(() => expect(renamed).toHaveBeenCalledWith("p-1", 2, "plate"));
    await waitFor(() => expect(onClassesChanged).toHaveBeenCalled());
  });

  it("explains a refused delete before attempting it", async () => {
    vi.spyOn(api, "getClassUsage").mockResolvedValue({ class_id: 2, label_count: 47 });
    const deleted = vi.spyOn(api, "deleteClass").mockResolvedValue(undefined);
    render(<ClassSchemaEditor project={project} />);

    (await screen.findByRole("button", { name: /delete number_plate/i })).click();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/47 label/i);
    expect(deleted).not.toHaveBeenCalled();
  });

  it("deletes a class nothing is using", async () => {
    vi.spyOn(api, "getClassUsage").mockResolvedValue({ class_id: 2, label_count: 0 });
    const deleted = vi.spyOn(api, "deleteClass").mockResolvedValue(undefined);
    render(<ClassSchemaEditor project={project} />);

    (await screen.findByRole("button", { name: /delete number_plate/i })).click();

    await waitFor(() => expect(deleted).toHaveBeenCalledWith("p-1", 2));
  });

  it("says so plainly when a project has no classes yet", async () => {
    vi.spyOn(api, "listClasses").mockResolvedValue([]);
    render(<ClassSchemaEditor project={project} />);

    expect(await screen.findByText(/no classes yet/i)).toBeInTheDocument();
  });
});
