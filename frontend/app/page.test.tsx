import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import Home from "./page";
import * as api from "../lib/api";

vi.mock("../components/readiness-panel", () => ({ ReadinessPanel: () => <p>API ready</p> }));

beforeEach(() => {
  vi.spyOn(api, "fetchCourses").mockResolvedValue([
    { subject: "Informatique", course_id: "course-1", document_count: 1, page_count: 10 },
    { subject: "Informatique", course_id: "course-2", document_count: 2, page_count: 30 },
  ]);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function chooseCourse(courseId: string) {
  await waitFor(() => expect(screen.getByRole("option", { name: new RegExp(courseId) })).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText("Course"), { target: { value: JSON.stringify({ courseId }) } });
}

describe("Course chat", () => {
  it("renders incremental text and citation provenance and prevents duplicate submission", async () => {
    let release!: () => void;
    const pending = new Promise<void>((resolve) => { release = resolve; });
    const request = vi.spyOn(api, "requestAnswerStream").mockImplementation(async (_course, _question, onEvent) => {
      onEvent({ version: 1, type: "delta", text: "Grounded answer" });
      onEvent({ version: 1, type: "citations", citations: [{ citation_id: "a", subject: "Informatique", course_id: "course-1", document_title: null, section_title: null, source_filename: "week1.pdf", physical_page_number: 2, excerpt: "A supporting excerpt." }] });
      await pending;
      onEvent({ version: 1, type: "completed" });
    });
    render(<Home />);
    await chooseCourse("course-1");
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "What?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByText("Grounded answer")).toBeInTheDocument());
    expect(screen.getByText(/week1.pdf/)).toBeInTheDocument();
    expect(screen.getByText(/p\. 2/)).toBeInTheDocument();
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
    await chooseCourse("course-1");
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
    await chooseCourse("course-1");
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "What?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Please clarify."));
  });
});


describe("course selection", () => {
  it("asks across all courses by default and labels each source with its course", async () => {
    const request = vi.spyOn(api, "requestAnswerStream").mockImplementation(async (_course, _question, onEvent) => {
      onEvent({ version: 1, type: "delta", text: "Answer" });
      onEvent({ version: 1, type: "citations", citations: [{ citation_id: "a", subject: "Maths", course_id: "math-200", document_title: "Analyse 1", section_title: "Chapitre 2 > Limites", source_filename: "calc.pdf", physical_page_number: 1, excerpt: "Excerpt." }] });
      onEvent({ version: 1, type: "completed" });
    });
    render(<Home />);
    expect(screen.getByLabelText("Course")).toHaveValue("{}");
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "What?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByText(/Maths › math-200/)).toBeInTheDocument());
    expect(screen.getByText("Analyse 1")).toBeInTheDocument();
    expect(screen.getByText("Chapitre 2 > Limites")).toBeInTheDocument();
    expect(request).toHaveBeenCalledWith({}, "What?", expect.any(Function));
  });

  it("shows an abstention as a notice rather than an answer", async () => {
    vi.spyOn(api, "requestAnswerStream").mockImplementation(async (_course, _question, onEvent) => {
      onEvent({ version: 1, type: "abstention", message: "Not in the course material." });
    });
    render(<Home />);
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "What?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByText("Not in the course material.")).toHaveClass("chat-notice"));
    expect(screen.queryByRole("heading", { name: "Answer" })).not.toBeInTheDocument();
  });

  it("still allows questions when the course list cannot load", async () => {
    vi.spyOn(api, "fetchCourses").mockRejectedValue(new Error("offline"));
    render(<Home />);
    await waitFor(() => expect(screen.getByText(/Could not load the course list/)).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "What?" } });
    expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
  });
});


describe("subject grouping", () => {
  it("groups courses under their subject and can ask across one subject", async () => {
    const request = vi.spyOn(api, "requestAnswerStream").mockImplementation(async (_scope, _question, onEvent) => {
      onEvent({ version: 1, type: "abstention", message: "Not found." });
    });
    render(<Home />);
    await waitFor(() => expect(screen.getByRole("group", { name: "Informatique" })).toBeInTheDocument());
    expect(screen.getByRole("option", { name: "All of Informatique" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Course"), { target: { value: JSON.stringify({ subject: "Informatique" }) } });
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "What?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith({ subject: "Informatique" }, "What?", expect.any(Function)));
  });

  it("sends a single course scope when a course is chosen", async () => {
    const request = vi.spyOn(api, "requestAnswerStream").mockImplementation(async (_scope, _question, onEvent) => {
      onEvent({ version: 1, type: "abstention", message: "Not found." });
    });
    render(<Home />);
    await chooseCourse("course-2");
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "What?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith({ courseId: "course-2" }, "What?", expect.any(Function)));
  });
});


describe("source labels", () => {
  it("shows a page only for PDF sources", async () => {
    vi.spyOn(api, "requestAnswerStream").mockImplementation(async (_scope, _question, onEvent) => {
      onEvent({ version: 1, type: "delta", text: "Answer" });
      onEvent({ version: 1, type: "citations", citations: [
        { citation_id: "a", subject: "Général", course_id: "stat", document_title: "Fiche de synthèse", section_title: "Variance", source_filename: "stat/fiche.md", physical_page_number: 1, excerpt: "Variance excerpt." },
        { citation_id: "b", subject: "Général", course_id: "stat", document_title: "Statistics", section_title: null, source_filename: "stat/deck.pdf", physical_page_number: 7, excerpt: "Deck excerpt." },
      ] });
      onEvent({ version: 1, type: "completed" });
    });
    render(<Home />);
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "Variance?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByText("Statistics")).toBeInTheDocument());
    const [markdown, pdf] = screen.getAllByRole("listitem");
    expect(markdown).not.toHaveTextContent("p. ");
    expect(pdf).toHaveTextContent("p. 7");
  });
});


describe("course list recovery", () => {
  it("loads the courses once the API comes back", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(api, "fetchCourses")
      .mockRejectedValueOnce(new Error("starting"))
      .mockResolvedValue([{ subject: "Général", course_id: "Cryptographie", document_count: 4, page_count: 218 }]);
    render(<Home />);

    await waitFor(() => expect(screen.getByText(/Could not load the course list/)).toBeInTheDocument());
    await vi.advanceTimersByTimeAsync(5000);
    await waitFor(() => expect(screen.getByRole("option", { name: /Cryptographie/ })).toBeInTheDocument());
    expect(screen.queryByText(/Could not load the course list/)).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});
