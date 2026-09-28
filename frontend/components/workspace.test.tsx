import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import * as api from "../lib/api";
import type { ViewerTarget } from "../lib/chat";
import { Workspace } from "./workspace";

// pdf.js cannot run in jsdom; the viewer is exercised through what it is asked to show.
vi.mock("./viewer/document-viewer", () => ({
  DocumentViewer: ({ target, onClose }: { target: ViewerTarget; onClose: () => void }) => (
    <div data-testid="viewer">
      doc={target.documentId} page={target.page} boxes={target.highlights.flatMap((item) => item.boxes).length}
      <button onClick={onClose}>fermer</button>
    </div>
  ),
}));
vi.mock("./readiness-panel", () => ({ ReadinessPanel: () => <p>Serveur connecté</p> }));

const citation: api.Citation = {
  number: 1,
  chunk_id: "c1",
  document_id: "slides",
  subject: "Général",
  course_id: "Cryptographie",
  document_title: "Introduction à la cryptologie",
  section_title: "RSA : Rivest Shamir Adleman",
  source_filename: "Cryptographie/slides.pdf",
  page: 52,
  excerpt: "Bob choisit deux grands nombres premiers p et q.",
  quotes: ["deux grands nombres premiers p et q"],
  highlights: [{ page: 52, boxes: [[0.1, 0.2, 0.6, 0.25]], lines: [] }],
};

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  vi.spyOn(api, "fetchCourses").mockResolvedValue([{ subject: "Général", course_id: "Cryptographie", document_count: 1, page_count: 211 }]);
  vi.spyOn(api, "fetchDocuments").mockResolvedValue([{ id: "slides", subject: "Général", course_id: "Cryptographie", title: "Introduction à la cryptologie", source_filename: "Cryptographie/slides.pdf", page_count: 211 }]);
  vi.spyOn(api, "fetchConversations").mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function ask(question: string) {
  fireEvent.change(screen.getByLabelText("Question"), { target: { value: question } });
  fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));
}

describe("Workspace", () => {
  it("streams an answer with source markers and opens the exact passage", async () => {
    const request = vi.spyOn(api, "requestAnswerStream").mockImplementation(async (_request, onEvent) => {
      onEvent({ version: 2, type: "conversation", conversation_id: "conv-1" });
      onEvent({ version: 2, type: "claim", index: 0, text: "RSA commence par choisir deux grands nombres premiers.", citations: [1] });
      onEvent({ version: 2, type: "citations", citations: [citation] });
      onEvent({ version: 2, type: "completed", message_id: "m1" });
    });
    render(<Workspace />);
    await ask("Comment fonctionne RSA ?");

    await waitFor(() => expect(screen.getByText(/deux grands nombres premiers\./)).toBeInTheDocument());
    expect(request).toHaveBeenCalledWith({ question: "Comment fonctionne RSA ?", conversation_id: null, scope: {} }, expect.any(Function), expect.anything());
    const sources = screen.getByRole("region", { name: "Sources" });
    expect(within(sources).getByText("Introduction à la cryptologie")).toBeInTheDocument();
    expect(within(sources).getByText(/page 52/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Source 1 :/ }));
    expect(screen.getByTestId("viewer")).toHaveTextContent("doc=slides page=52 boxes=1");
    expect(window.location.search).toBe("?c=conv-1&doc=slides&p=52");
  });

  it("continues the same conversation for follow-up questions", async () => {
    const request = vi.spyOn(api, "requestAnswerStream").mockImplementation(async (_request, onEvent) => {
      onEvent({ version: 2, type: "conversation", conversation_id: "conv-1" });
      onEvent({ version: 2, type: "abstention", message: "Je n'ai pas trouvé cette information." });
    });
    render(<Workspace />);
    await ask("Première question");
    await waitFor(() => expect(screen.getByText("Je n'ai pas trouvé cette information.")).toBeInTheDocument());
    await ask("Et ensuite ?");
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    expect(request.mock.calls[1][0]).toMatchObject({ question: "Et ensuite ?", conversation_id: "conv-1" });
  });

  it("choosing a document opens it and scopes questions to it", async () => {
    const request = vi.spyOn(api, "requestAnswerStream").mockImplementation(async (_request, onEvent) => {
      onEvent({ version: 2, type: "conversation", conversation_id: "c" });
      onEvent({ version: 2, type: "abstention", message: "Rien." });
    });
    render(<Workspace />);
    fireEvent.click(await screen.findByRole("button", { name: "Déplier Cryptographie" }));
    fireEvent.click(screen.getByRole("button", { name: "Introduction à la cryptologie" }));

    expect(screen.getByTestId("viewer")).toHaveTextContent("doc=slides page=1");
    expect(screen.getByText("Document : Introduction à la cryptologie")).toBeInTheDocument();
    await ask("Résume ce document");
    await waitFor(() => expect(request).toHaveBeenCalled());
    expect(request.mock.calls[0][0].scope).toEqual({ document_id: "slides" });

    fireEvent.click(screen.getByRole("button", { name: "Chercher dans tous les cours" }));
    expect(screen.getByText("Tous les cours", { selector: "span" })).toBeInTheDocument();
  });

  it("reopens a saved conversation with its answers and scope", async () => {
    vi.spyOn(api, "fetchConversations").mockResolvedValue([{ id: "conv-9", title: "RSA", scope: { course_id: "Cryptographie" }, updated_at: new Date().toISOString() }]);
    vi.spyOn(api, "fetchConversation").mockResolvedValue({
      id: "conv-9",
      title: "RSA",
      scope: { course_id: "Cryptographie" },
      updated_at: new Date().toISOString(),
      messages: [
        { id: "u", role: "user", content: "Comment fonctionne RSA ?", payload: {}, created_at: "" },
        { id: "a", role: "assistant", content: "…", payload: { status: "answered", claims: [{ text: "Réponse enregistrée.", citations: [1] }], citations: [citation] }, created_at: "" },
      ],
    });
    render(<Workspace />);
    fireEvent.click(await screen.findByRole("button", { name: "RSA" }));

    await waitFor(() => expect(screen.getByText(/Réponse enregistrée\./)).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "RSA" })).toBeInTheDocument();
    const request = vi.spyOn(api, "requestAnswerStream").mockImplementation(async (_request, onEvent) => {
      onEvent({ version: 2, type: "abstention", message: "Rien." });
    });
    await ask("Et la signature ?");
    await waitFor(() => expect(request).toHaveBeenCalled());
    expect(request.mock.calls[0][0]).toEqual({ question: "Et la signature ?", conversation_id: "conv-9", scope: { course_id: "Cryptographie" } });
  });

  it("shows a clear error when the server cannot be reached", async () => {
    vi.spyOn(api, "requestAnswerStream").mockRejectedValue(new Error("offline"));
    render(<Workspace />);
    await ask("Bonjour ?");
    await waitFor(() => expect(screen.getByText(/La connexion au serveur a échoué/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Envoyer" })).toBeInTheDocument();
  });
});
