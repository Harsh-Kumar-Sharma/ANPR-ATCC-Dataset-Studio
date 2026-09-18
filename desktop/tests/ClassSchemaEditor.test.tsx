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

  it("deletes a class nothing is using without asking", async () => {
    vi.spyOn(api, "getClassUsage").mockResolvedValue({ class_id: 2, label_count: 0 });
    const deleted = vi.spyOn(api, "deleteClass").mockResolvedValue({ class_id: 2, remapped: 0, deleted_labels: 0 });
    render(<ClassSchemaEditor project={project} />);

    (await screen.findByRole("button", { name: /delete number_plate/i })).click();

    await waitFor(() => expect(deleted).toHaveBeenCalledWith("p-1", 2));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("asks where the labels go when a class is in use, with the real count", async () => {
    vi.spyOn(api, "getClassUsage").mockResolvedValue({ class_id: 2, label_count: 47 });
    const deleted = vi.spyOn(api, "deleteClass").mockResolvedValue({ class_id: 2, remapped: 47, deleted_labels: 0 });
    render(<ClassSchemaEditor project={project} />);

    (await screen.findByRole("button", { name: /delete number_plate/i })).click();

    const dialog = await screen.findByRole("dialog", { name: /delete number_plate/i });
    expect(dialog).toHaveTextContent(/47 label/);
    expect(dialog).toHaveTextContent(/move them where, or delete them/i);
    expect(deleted).not.toHaveBeenCalled();
  });

  it("remaps the labels onto the chosen class", async () => {
    vi.spyOn(api, "getClassUsage").mockResolvedValue({ class_id: 2, label_count: 47 });
    const deleted = vi.spyOn(api, "deleteClass").mockResolvedValue({ class_id: 2, remapped: 47, deleted_labels: 0 });
    render(<ClassSchemaEditor project={project} />);

    (await screen.findByRole("button", { name: /delete number_plate/i })).click();
    await screen.findByRole("dialog");

    // Only the other class is offered as a destination.
    const select = screen.getByLabelText(/move to/i) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(["vehicle"]);

    screen.getByRole("button", { name: /^move$/i }).click();

    await waitFor(() => expect(deleted).toHaveBeenCalledWith("p-1", 2, { remapTo: 1 }));
  });

  it("deletes the labels with the class when told to", async () => {
    vi.spyOn(api, "getClassUsage").mockResolvedValue({ class_id: 2, label_count: 47 });
    const deleted = vi.spyOn(api, "deleteClass").mockResolvedValue({ class_id: 2, remapped: 0, deleted_labels: 47 });
    render(<ClassSchemaEditor project={project} />);

    (await screen.findByRole("button", { name: /delete number_plate/i })).click();
    await screen.findByRole("dialog");

    screen.getByRole("button", { name: /delete the labels too/i }).click();

    await waitFor(() => expect(deleted).toHaveBeenCalledWith("p-1", 2, { deleteLabels: true }));
  });

  it("lets the user back out and keep the class", async () => {
    vi.spyOn(api, "getClassUsage").mockResolvedValue({ class_id: 2, label_count: 47 });
    const deleted = vi.spyOn(api, "deleteClass").mockResolvedValue({ class_id: 2, remapped: 0, deleted_labels: 0 });
    render(<ClassSchemaEditor project={project} />);

    (await screen.findByRole("button", { name: /delete number_plate/i })).click();
    await screen.findByRole("dialog");

    screen.getByRole("button", { name: /keep the class/i }).click();

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(deleted).not.toHaveBeenCalled();
  });

  it("offers only deletion when there is no other class to move to", async () => {
    vi.spyOn(api, "listClasses").mockResolvedValue([cls(1, "only")]);
    vi.spyOn(api, "getClassUsage").mockResolvedValue({ class_id: 1, label_count: 3 });
    render(<ClassSchemaEditor project={project} />);

    (await screen.findByRole("button", { name: /delete only/i })).click();

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/no other class/i);
    expect(screen.queryByRole("button", { name: /^move$/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /delete the labels too/i })).toBeInTheDocument();
  });

  it("says so plainly when a project has no classes yet", async () => {
    vi.spyOn(api, "listClasses").mockResolvedValue([]);
    render(<ClassSchemaEditor project={project} />);

    expect(await screen.findByText(/no classes yet/i)).toBeInTheDocument();
  });
});
