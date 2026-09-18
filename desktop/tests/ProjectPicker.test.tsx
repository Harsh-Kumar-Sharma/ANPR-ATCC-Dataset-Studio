import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import ProjectPicker from "../src/components/ProjectPicker";

describe("ProjectPicker", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listProjects").mockResolvedValue([]);
  });

  it("lets the user choose which classes a new project starts with", async () => {
    render(<ProjectPicker onSelect={vi.fn()} />);

    const select = await screen.findByLabelText(/classes/i);
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.value);

    expect(options).toEqual(["atcc-v1", "anpr-v1", "blank"]);
  });

  it("creates the project with the chosen preset", async () => {
    const created = vi.spyOn(api, "createProject").mockResolvedValue({
      id: "p-1",
      name: "Plates",
      created_at: "2026-09-19T00:00:00+00:00",
      class_schema_version: "anpr-v1",
      workspace_path: "/workspace/p-1",
    });

    render(<ProjectPicker onSelect={vi.fn()} />);

    const input = await screen.findByPlaceholderText(/new project name/i);
    const select = screen.getByLabelText(/classes/i) as HTMLSelectElement;

    // Drive the controlled inputs the way React expects.
    const setValue = (el: HTMLInputElement | HTMLSelectElement, value: string) => {
      const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
      el.dispatchEvent(new Event("change", { bubbles: true }));
    };
    setValue(input as HTMLInputElement, "Plates");
    setValue(select, "anpr-v1");

    screen.getByRole("button", { name: /^create$/i }).click();

    await waitFor(() => expect(created).toHaveBeenCalledWith("Plates", "anpr-v1"));
  });

  it("defaults to the traffic-survey classes every project used to have", async () => {
    render(<ProjectPicker onSelect={vi.fn()} />);

    const select = (await screen.findByLabelText(/classes/i)) as HTMLSelectElement;

    expect(select.value).toBe("atcc-v1");
  });
});
