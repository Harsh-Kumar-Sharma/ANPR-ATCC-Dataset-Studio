/**
 * The database is behind the app, and that is why something failed.
 *
 * What it looked like before: a 500 and "An unexpected error
 * occurred" in whichever panel happened to use the newest table. The
 * user goes looking for a bug that is not there.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api";
import SchemaBanner from "../src/components/SchemaBanner";
import type { SchemaState } from "../src/types";

const behind: SchemaState = {
  current: "c81d4e0a7f36",
  head: "d92a3b5c1e04",
  up_to_date: false,
  pending: ["d92a3b5c1e04"],
};

const current: SchemaState = { current: "d92a3b5c1e04", head: "d92a3b5c1e04", up_to_date: true, pending: [] };

describe("SchemaBanner", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("says nothing when the database is current", async () => {
    vi.spyOn(api, "getSchemaState").mockResolvedValue(current);
    const { container } = render(<SchemaBanner />);

    await waitFor(() => expect(api.getSchemaState).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("says so when it is behind, and what that means", async () => {
    vi.spyOn(api, "getSchemaState").mockResolvedValue(behind);
    render(<SchemaBanner />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/newer database/i);
    expect(screen.getByRole("alert")).toHaveTextContent(/will fail until/i);
  });

  it("brings it up to date, and says where the copy went", async () => {
    vi.spyOn(api, "getSchemaState").mockResolvedValue(behind);
    vi.spyOn(api, "upgradeSchema").mockResolvedValue({
      state: current,
      backup_path: "data/backups/app-20260920.db",
    });
    const onUpgraded = vi.fn();
    render(<SchemaBanner onUpgraded={onUpgraded} />);

    fireEvent.click(await screen.findByRole("button", { name: /update the database/i }));

    expect(await screen.findByText(/app-20260920\.db/)).toBeInTheDocument();
    expect(onUpgraded).toHaveBeenCalled();
  });

  it("promises the copy before the button is pressed, not after", async () => {
    // An irreversible-looking button with no stated safety net is one
    // nobody presses.
    vi.spyOn(api, "getSchemaState").mockResolvedValue(behind);
    render(<SchemaBanner />);

    expect(await screen.findByText(/copy is saved first/i)).toBeInTheDocument();
  });

  it("says why an upgrade failed", async () => {
    vi.spyOn(api, "getSchemaState").mockResolvedValue(behind);
    vi.spyOn(api, "upgradeSchema").mockRejectedValue(
      new ApiError(400, "schema_upgrade_failed", "Could not back up the database: disk full."),
    );
    render(<SchemaBanner />);

    fireEvent.click(await screen.findByRole("button", { name: /update the database/i }));

    expect(await screen.findByText(/disk full/i)).toBeInTheDocument();
  });

  it("stays quiet when it cannot tell", async () => {
    // An app that cannot say whether its database is current should
    // not cry wolf on a blip.
    vi.spyOn(api, "getSchemaState").mockRejectedValue(new Error("offline"));
    const { container } = render(<SchemaBanner />);

    await waitFor(() => expect(api.getSchemaState).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
