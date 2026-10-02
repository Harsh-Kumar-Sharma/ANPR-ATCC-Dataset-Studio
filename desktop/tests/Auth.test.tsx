import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, setAuthToken } from "../src/api";
import App from "../src/App";

const admin = {
  id: "u-1",
  username: "harsh",
  display_name: "Harsh Sharma",
  role: "admin",
  is_active: true,
  created_at: "2026-10-03T00:00:00",
  last_login_at: null,
};

const labeller = { ...admin, id: "u-2", username: "ravi", display_name: "Ravi", role: "user" };

const project = {
  id: "p-1",
  name: "Gantry 7",
  created_at: "2026-09-19T00:00:00+00:00",
  class_schema_version: "atcc-v1",
  workspace_path: "/workspace/p-1",
};

function signedIn(user: typeof admin) {
  return { token: "tok", expires_at: "2026-10-10T00:00:00", user };
}

beforeEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  setAuthToken(null);
  vi.spyOn(api, "getSchemaState").mockResolvedValue({ current: "x", head: "x", up_to_date: true, pending: [] });
  vi.spyOn(api, "listProjects").mockResolvedValue([project as never]);
});

describe("signing in", () => {
  it("asks to sign in before showing any project", async () => {
    vi.spyOn(api, "getAuthStatus").mockResolvedValue({ needs_setup: false, database_ready: true });
    render(<App />);

    expect(await screen.findByRole("button", { name: /^sign in$/i })).toBeInTheDocument();
    expect(screen.queryByText("Gantry 7")).not.toBeInTheDocument();
    expect(api.listProjects).not.toHaveBeenCalled();
  });

  it("shows the projects once signed in", async () => {
    vi.spyOn(api, "getAuthStatus").mockResolvedValue({ needs_setup: false, database_ready: true });
    const login = vi.spyOn(api, "login").mockResolvedValue(signedIn(labeller) as never);
    render(<App />);

    fireEvent.change(await screen.findByLabelText(/username/i), { target: { value: "ravi" } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: "secret-pass" } });
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));

    expect(await screen.findByText("Gantry 7")).toBeInTheDocument();
    expect(login).toHaveBeenCalledWith("ravi", "secret-pass");
  });

  it("says what was wrong and stays on the form", async () => {
    vi.spyOn(api, "getAuthStatus").mockResolvedValue({ needs_setup: false, database_ready: true });
    const { ApiError } = await import("../src/api");
    vi.spyOn(api, "login").mockRejectedValue(new ApiError(401, "invalid_credentials", "Wrong username or password."));
    render(<App />);

    fireEvent.change(await screen.findByLabelText(/username/i), { target: { value: "ravi" } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: "nope-nope" } });
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));

    expect(await screen.findByText(/wrong username or password/i)).toBeInTheDocument();
    expect(screen.queryByText("Gantry 7")).not.toBeInTheDocument();
  });

  it("creates the first admin when nobody exists yet", async () => {
    vi.spyOn(api, "getAuthStatus").mockResolvedValue({ needs_setup: true, database_ready: true });
    const setup = vi.spyOn(api, "setupAdmin").mockResolvedValue(signedIn(admin) as never);
    render(<App />);

    fireEvent.change(await screen.findByLabelText(/username/i), { target: { value: "harsh" } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: "secret-pass" } });
    fireEvent.change(screen.getByLabelText(/confirm password/i), { target: { value: "secret-pass" } });
    fireEvent.click(screen.getByRole("button", { name: /create admin/i }));

    expect(await screen.findByText("Gantry 7")).toBeInTheDocument();
    expect(setup).toHaveBeenCalledWith("harsh", "secret-pass", "");
  });

  it("refuses two passwords that differ before asking the server", async () => {
    vi.spyOn(api, "getAuthStatus").mockResolvedValue({ needs_setup: true, database_ready: true });
    const setup = vi.spyOn(api, "setupAdmin");
    render(<App />);

    fireEvent.change(await screen.findByLabelText(/username/i), { target: { value: "harsh" } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: "secret-pass" } });
    fireEvent.change(screen.getByLabelText(/confirm password/i), { target: { value: "secret-pasz" } });
    fireEvent.click(screen.getByRole("button", { name: /create admin/i }));

    expect(await screen.findByText(/do not match/i)).toBeInTheDocument();
    expect(setup).not.toHaveBeenCalled();
  });

  it("goes back to the sign-in screen on signing out", async () => {
    setAuthToken("tok");
    vi.spyOn(api, "getMe").mockResolvedValue(admin as never);
    vi.spyOn(api, "getAuthStatus").mockResolvedValue({ needs_setup: false, database_ready: true });
    const logout = vi.spyOn(api, "logout").mockResolvedValue(undefined);
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /account: harsh sharma/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /sign out/i }));

    expect(await screen.findByRole("button", { name: /^sign in$/i })).toBeInTheDocument();
    expect(logout).toHaveBeenCalled();
  });
});

describe("managing users", () => {
  it("is offered to an admin and not to a user", async () => {
    setAuthToken("tok");
    vi.spyOn(api, "getMe").mockResolvedValue(labeller as never);
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /account: ravi/i }));
    expect(screen.getByRole("menuitem", { name: /change password/i })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /manage users/i })).not.toBeInTheDocument();
  });

  it("lists users and adds one", async () => {
    setAuthToken("tok");
    vi.spyOn(api, "getMe").mockResolvedValue(admin as never);
    const list = vi.spyOn(api, "listUsers").mockResolvedValue([admin as never]);
    const create = vi.spyOn(api, "createUser").mockResolvedValue(labeller as never);
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /account: harsh sharma/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /manage users/i }));
    expect(await screen.findByText("@harsh")).toBeInTheDocument();

    list.mockResolvedValue([admin as never, labeller as never]);
    fireEvent.click(screen.getByRole("button", { name: /add user/i }));
    const dialog = screen.getByRole("dialog", { name: "Users" });
    fireEvent.change(dialog.querySelector("form input[type=text]")!, { target: { value: "ravi" } });
    fireEvent.change(dialog.querySelector("form input[type=password]")!, { target: { value: "secret-pass" } });
    const submit = Array.from(dialog.querySelectorAll("form button[type=submit]"))[0] as HTMLButtonElement;
    fireEvent.click(submit);

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({ username: "ravi", password: "secret-pass", display_name: "", role: "user" }),
    );
    expect(await screen.findByText("@ravi")).toBeInTheDocument();
  });

  it("does not offer to delete or switch off yourself", async () => {
    setAuthToken("tok");
    vi.spyOn(api, "getMe").mockResolvedValue(admin as never);
    vi.spyOn(api, "listUsers").mockResolvedValue([admin as never, labeller as never]);
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /account: harsh sharma/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /manage users/i }));
    await screen.findByText("@ravi");

    expect(screen.queryByRole("button", { name: /delete harsh/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /delete ravi/i })).toBeInTheDocument();
  });
});
