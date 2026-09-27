import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { READINESS_RETRY_MS, ReadinessPanel } from "./readiness-panel";
import * as api from "../lib/api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
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

  it("keeps checking and recovers once the API starts answering", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const readiness = vi.spyOn(api, "fetchReadiness")
      .mockRejectedValueOnce(new Error("starting"))
      .mockResolvedValue({ status: "ready", service: "course-rag-api" });
    render(<ReadinessPanel />);

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("API unavailable"));
    await vi.advanceTimersByTimeAsync(READINESS_RETRY_MS);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("API ready"));
    expect(readiness).toHaveBeenCalledTimes(2);
  });
});
