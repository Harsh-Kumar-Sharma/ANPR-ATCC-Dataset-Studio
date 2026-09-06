import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "../src/App";

describe("App", () => {
  it("renders the studio title", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: /ANPR \+ ATCC Dataset Studio/i })).toBeInTheDocument();
  });
});
