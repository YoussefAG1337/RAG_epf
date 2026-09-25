import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { ReadinessPanel } from "./readiness-panel";
import * as api from "../lib/api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ReadinessPanel", () => {
  it("reports ready when the API responds", async () => {
    vi.spyOn(api, "fetchReadiness").mockResolvedValue({ status: "ready", service: "course-rag-api" });
    render(<ReadinessPanel />);

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("API ready"));
  });

  it("reports unavailable when the API cannot be reached", async () => {
    vi.spyOn(api, "fetchReadiness").mockRejectedValue(new Error("offline"));
    render(<ReadinessPanel />);

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("API unavailable"));
  });
});
