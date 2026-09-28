// Typed client for the course assistant API.

export const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type Readiness = { status: "ready"; service: "course-rag-api" };
export type Course = { subject: string; course_id: string; document_count: number; page_count: number };
export type DocumentSummary = {
  id: string;
  subject: string;
  course_id: string;
  title: string;
  source_filename: string;
  page_count: number;
};
export type DocumentKind = "pdf" | "markdown" | "text" | "spreadsheet";
export type DocumentInfo = DocumentSummary & { kind: DocumentKind };
export type DocumentContent = {
  kind: DocumentKind;
  lines: { line: number; text: string }[];
  sheets: { number: number; name: string; rows: { row: number; cells: string[] }[] }[];
};

/** Which material to search: everything, a subject, a course, or one document. */
export type Scope = { subject?: string; course_id?: string; document_id?: string };

/** Where to highlight: PDF boxes are [x0, y0, x1, y1] as fractions of the page. */
export type Highlight = { page: number; boxes: number[][]; lines: number[] };
export type Citation = {
  number: number;
  chunk_id: string;
  document_id: string;
  subject: string;
  course_id: string;
  document_title: string | null;
  section_title: string | null;
  source_filename: string;
  page: number;
  excerpt: string;
  quotes: string[];
  highlights: Highlight[];
};
export type Claim = { text: string; citations: number[] };

export type AnswerStreamEvent =
  | { version: 2; type: "conversation"; conversation_id: string }
  | { version: 2; type: "status"; stage: "searching" | "writing"; retrieval_query: string | null }
  | { version: 2; type: "claim"; index: number; text: string; citations: number[] }
  | { version: 2; type: "citations"; citations: Citation[] }
  | { version: 2; type: "completed"; message_id: string | null }
  | { version: 2; type: "abstention" | "error"; message: string };

export type AnswerStatus = "answered" | "abstained" | "error";
export type ConversationSummary = { id: string; title: string; scope: Scope; updated_at: string };
export type StoredMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  payload: { status?: AnswerStatus; claims?: Claim[]; citations?: Citation[]; retrieval_query?: string; scope?: Scope };
  created_at: string;
};
export type ConversationDetail = ConversationSummary & { messages: StoredMessage[] };

async function getJson<T>(path: string, fetcher: typeof fetch): Promise<T> {
  const response = await fetcher(`${apiBaseUrl}${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`La requête ${path} a échoué (${response.status})`);
  return (await response.json()) as T;
}

export async function fetchReadiness(fetcher: typeof fetch = fetch): Promise<Readiness> {
  const body = await getJson<Record<string, unknown>>("/api/v1/readiness", fetcher);
  if (body.status !== "ready" || body.service !== "course-rag-api") {
    throw new Error("Readiness response has an invalid shape");
  }
  return { status: "ready", service: "course-rag-api" };
}

export async function fetchCourses(fetcher: typeof fetch = fetch): Promise<Course[]> {
  const body = await getJson<{ courses?: unknown }>("/api/v1/courses", fetcher);
  if (!Array.isArray(body.courses)) throw new Error("Courses response has an invalid shape");
  return body.courses as Course[];
}

export async function fetchDocuments(fetcher: typeof fetch = fetch): Promise<DocumentSummary[]> {
  const body = await getJson<{ documents?: unknown }>("/api/v1/documents", fetcher);
  if (!Array.isArray(body.documents)) throw new Error("Documents response has an invalid shape");
  return body.documents as DocumentSummary[];
}

export function fetchDocument(id: string, fetcher: typeof fetch = fetch): Promise<DocumentInfo> {
  return getJson<DocumentInfo>(`/api/v1/documents/${encodeURIComponent(id)}`, fetcher);
}

export function fetchDocumentContent(id: string, fetcher: typeof fetch = fetch): Promise<DocumentContent> {
  return getJson<DocumentContent>(`/api/v1/documents/${encodeURIComponent(id)}/content`, fetcher);
}

export function documentFileUrl(id: string): string {
  return `${apiBaseUrl}/api/v1/documents/${encodeURIComponent(id)}/file`;
}

export async function fetchConversations(fetcher: typeof fetch = fetch): Promise<ConversationSummary[]> {
  const body = await getJson<{ conversations?: unknown }>("/api/v1/conversations", fetcher);
  if (!Array.isArray(body.conversations)) throw new Error("Conversations response has an invalid shape");
  return body.conversations as ConversationSummary[];
}

export function fetchConversation(id: string, fetcher: typeof fetch = fetch): Promise<ConversationDetail> {
  return getJson<ConversationDetail>(`/api/v1/conversations/${encodeURIComponent(id)}`, fetcher);
}

export async function renameConversation(id: string, title: string, fetcher: typeof fetch = fetch): Promise<ConversationSummary> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/conversations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!response.ok) throw new Error(`Renommage impossible (${response.status})`);
  return (await response.json()) as ConversationSummary;
}

export async function deleteConversation(id: string, fetcher: typeof fetch = fetch): Promise<void> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!response.ok && response.status !== 404) throw new Error(`Suppression impossible (${response.status})`);
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isNumberArray = (value: unknown): value is number[] =>
  Array.isArray(value) && value.every((item) => Number.isInteger(item));

function parseEvent(line: string): AnswerStreamEvent {
  let raw: unknown;
  try {
    raw = JSON.parse(line);
  } catch {
    throw new Error("Le flux de réponse contient du JSON invalide");
  }
  if (!isRecord(raw) || raw.version !== 2 || typeof raw.type !== "string") {
    throw new Error("Le flux de réponse contient un événement invalide");
  }
  const invalid = () => new Error(`Événement « ${String(raw.type)} » invalide`);
  switch (raw.type) {
    case "conversation":
      if (typeof raw.conversation_id !== "string") throw invalid();
      return { version: 2, type: "conversation", conversation_id: raw.conversation_id };
    case "status":
      if (raw.stage !== "searching" && raw.stage !== "writing") throw invalid();
      return {
        version: 2,
        type: "status",
        stage: raw.stage,
        retrieval_query: typeof raw.retrieval_query === "string" ? raw.retrieval_query : null,
      };
    case "claim":
      if (!Number.isInteger(raw.index) || typeof raw.text !== "string" || !isNumberArray(raw.citations)) throw invalid();
      return { version: 2, type: "claim", index: raw.index as number, text: raw.text, citations: raw.citations };
    case "citations":
      if (!Array.isArray(raw.citations) || !raw.citations.every((item) => isRecord(item) && Number.isInteger(item.number) && typeof item.document_id === "string" && Array.isArray(item.highlights))) {
        throw invalid();
      }
      return { version: 2, type: "citations", citations: raw.citations as Citation[] };
    case "completed":
      return { version: 2, type: "completed", message_id: typeof raw.message_id === "string" ? raw.message_id : null };
    case "abstention":
    case "error":
      if (typeof raw.message !== "string" || !raw.message) throw invalid();
      return { version: 2, type: raw.type, message: raw.message };
    default:
      throw new Error("Le flux de réponse contient un événement inconnu");
  }
}

/** Read an NDJSON answer stream, validating each event, until a terminal event. */
export async function consumeAnswerStream(response: Response, onEvent: (event: AnswerStreamEvent) => void): Promise<void> {
  if (!response.ok || !response.body) throw new Error(`La requête a échoué (${response.status})`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let terminal = false;
  const accept = (line: string) => {
    if (!line.trim()) return;
    if (terminal) throw new Error("Le flux contient un événement après la fin");
    const event = parseEvent(line);
    if (event.type === "completed" || event.type === "abstention" || event.type === "error") terminal = true;
    onEvent(event);
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      let newline = pending.indexOf("\n");
      while (newline >= 0) {
        accept(pending.slice(0, newline));
        pending = pending.slice(newline + 1);
        newline = pending.indexOf("\n");
      }
      if (done) break;
    }
    if (pending.trim()) accept(pending);
    if (!terminal) throw new Error("La réponse s'est interrompue");
  } catch (error) {
    await reader.cancel(error).catch(() => undefined);
    throw error;
  } finally {
    reader.releaseLock();
  }
}

export async function requestAnswerStream(
  request: { question: string; conversation_id?: string | null; scope: Scope },
  onEvent: (event: AnswerStreamEvent) => void,
  options: { fetcher?: typeof fetch; signal?: AbortSignal } = {},
): Promise<void> {
  const fetcher = options.fetcher ?? fetch;
  const response = await fetcher(`${apiBaseUrl}/api/v1/answers/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question: request.question, conversation_id: request.conversation_id ?? null, scope: request.scope }),
    cache: "no-store",
    signal: options.signal,
  });
  await consumeAnswerStream(response, onEvent);
}
