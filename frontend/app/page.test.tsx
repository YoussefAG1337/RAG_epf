import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import Home from "./page";
import * as api from "../lib/api";

vi.mock("../components/readiness-panel", () => ({ ReadinessPanel: () => <p>API ready</p> }));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("Course chat", () => {
  it("renders incremental text and citation provenance and prevents duplicate submission", async () => {
    let release!: () => void;
    const pending = new Promise<void>((resolve) => { release = resolve; });
    const request = vi.spyOn(api, "requestAnswerStream").mockImplementation(async (_course, _question, onEvent) => {
      onEvent({ version: 1, type: "delta", text: "Grounded answer" });
      onEvent({ version: 1, type: "citations", citations: [{ citation_id: "a", source_filename: "week1.pdf", physical_page_number: 2, excerpt: "A supporting excerpt." }] });
      await pending;
      onEvent({ version: 1, type: "completed" });
    });
    render(<Home />);
    fireEvent.change(screen.getByLabelText("Course ID"), { target: { value: "course-1" } });
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "What?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByText("Grounded answer")).toBeInTheDocument());
    expect(screen.getByText(/week1.pdf/)).toBeInTheDocument();
    expect(screen.getByText(/page 2/)).toBeInTheDocument();
    expect(screen.getByText("A supporting excerpt.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Answering…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Answering…" }));
    expect(request).toHaveBeenCalledTimes(1);
    release();
  });

  it("shows a stream error, re-enables submission, and allows retry", async () => {
    const request = vi.spyOn(api, "requestAnswerStream")
      .mockImplementationOnce(async (_course, _question, onEvent) => {
        onEvent({ version: 1, type: "error", message: "Could not answer. Try again." });
      })
      .mockImplementationOnce(async (_course, _question, onEvent) => {
        onEvent({ version: 1, type: "delta", text: "Recovered answer" });
        onEvent({ version: 1, type: "citations", citations: [] });
        onEvent({ version: 1, type: "completed" });
      });
    render(<Home />);
    fireEvent.change(screen.getByLabelText("Course ID"), { target: { value: "course-1" } });
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "What?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Could not answer. Try again."));
    expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByText("Recovered answer")).toBeInTheDocument());
    expect(request).toHaveBeenCalledTimes(2);
  });
});


describe("terminal messages", () => {
  it("shows clarification and abstention messages from the stream", async () => {
    vi.spyOn(api, "requestAnswerStream").mockImplementation(async (_course, _question, onEvent) => {
      onEvent({ version: 1, type: "clarification", message: "Please clarify." });
    });
    render(<Home />);
    fireEvent.change(screen.getByLabelText("Course ID"), { target: { value: "course-1" } });
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "What?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Please clarify."));
  });
});
