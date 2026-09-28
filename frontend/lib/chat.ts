import type { AnswerStreamEvent, Citation, Claim, DocumentSummary, Highlight, Scope, StoredMessage } from "./api";

export type UserMessage = { id: string; role: "user"; content: string };
export type AssistantMessage = {
  id: string;
  role: "assistant";
  status: "streaming" | "answered" | "abstained" | "error";
  stage: "searching" | "writing" | null;
  claims: Claim[];
  citations: Citation[];
  message: string | null;
  retrievalQuery: string | null;
};
export type ChatMessage = UserMessage | AssistantMessage;

/** What the document viewer should show: a document, a page, and what to highlight. */
export type ViewerTarget = {
  documentId: string;
  page: number;
  highlights: Highlight[];
  citationNumber: number | null;
  /** Changes on every open so reopening the same citation scrolls again. */
  nonce: number;
};

export function pendingAnswer(id: string): AssistantMessage {
  return { id, role: "assistant", status: "streaming", stage: "searching", claims: [], citations: [], message: null, retrievalQuery: null };
}

/** Apply one stream event to the answer being streamed. */
export function applyEvent(message: AssistantMessage, event: AnswerStreamEvent): AssistantMessage {
  switch (event.type) {
    case "status":
      return { ...message, stage: event.stage, retrievalQuery: event.retrieval_query ?? message.retrievalQuery };
    case "claim":
      return { ...message, stage: "writing", claims: [...message.claims, { text: event.text, citations: event.citations }] };
    case "citations":
      return { ...message, citations: event.citations };
    case "completed":
      return { ...message, status: "answered", stage: null };
    case "abstention":
      return { ...message, status: "abstained", stage: null, claims: [], citations: [], message: event.message };
    case "error":
      return { ...message, status: "error", stage: null, message: event.message };
    default:
      return message;
  }
}

/** Rebuild displayable messages from a saved conversation. */
export function fromStored(messages: StoredMessage[]): ChatMessage[] {
  let lastQuestion = "";
  return messages.map((stored): ChatMessage => {
    if (stored.role === "user") {
      lastQuestion = stored.content;
      return { id: stored.id, role: "user", content: stored.content };
    }
    const status = stored.payload.status ?? "answered";
    return {
      id: stored.id,
      role: "assistant",
      status,
      stage: null,
      claims: status === "answered" ? stored.payload.claims ?? [] : [],
      citations: status === "answered" ? stored.payload.citations ?? [] : [],
      message: status === "answered" ? null : stored.content,
      // Only a rewritten follow-up is worth showing.
      retrievalQuery: stored.payload.retrieval_query && stored.payload.retrieval_query !== lastQuestion ? stored.payload.retrieval_query : null,
    };
  });
}

/** Open a citation at its first highlighted page (or its chunk's page). */
export function citationTarget(citation: Citation, nonce: number): ViewerTarget {
  const first = citation.highlights[0];
  return {
    documentId: citation.document_id,
    page: first?.page ?? citation.page,
    highlights: citation.highlights,
    citationNumber: citation.number,
    nonce,
  };
}

export function citationLabel(citation: Citation): string {
  return citation.document_title || citation.source_filename.split("/").pop() || citation.source_filename;
}

export function isPaginated(filename: string): boolean {
  return filename.toLowerCase().endsWith(".pdf");
}

export function scopeLabel(scope: Scope, documents: DocumentSummary[]): string {
  if (scope.document_id) {
    return documents.find((document) => document.id === scope.document_id)?.title ?? "Document sélectionné";
  }
  if (scope.course_id) return scope.course_id;
  if (scope.subject) return `Toute la matière ${scope.subject}`;
  return "Tous les cours";
}

export function sameScope(a: Scope, b: Scope): boolean {
  return (a.subject ?? null) === (b.subject ?? null) && (a.course_id ?? null) === (b.course_id ?? null) && (a.document_id ?? null) === (b.document_id ?? null);
}
