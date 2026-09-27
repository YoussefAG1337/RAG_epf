export type Readiness = {
  status: "ready";
  service: "course-rag-api";
};

export type Citation = { citation_id: string; subject: string; course_id: string; document_title: string | null; section_title: string | null; source_filename: string; physical_page_number: number; excerpt: string };
export type Course = { subject: string; course_id: string; document_count: number; page_count: number };
/** Which material to search: one course, every course in a subject, or (empty) everything. */
export type AnswerScope = { subject?: string; courseId?: string };
export type AnswerStreamEvent =
  | { version: 1; type: "delta"; text: string }
  | { version: 1; type: "citations"; citations: Citation[] }
  | { version: 1; type: "completed" }
  | { version: 1; type: "clarification" | "abstention"; message: string }
  | { version: 1; type: "error"; message: string };

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function consumeAnswerStream(
  response: Response,
  onEvent: (event: AnswerStreamEvent) => void,
): Promise<void> {
  if (!response.ok || !response.body) throw new Error(`Answer stream request failed with status ${response.status}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  const state: { value: "deltas" | "citations" | "terminal" } = { value: "deltas" };
  const exactKeys = (value: Record<string, unknown>, keys: string[]) =>
    Object.keys(value).length === keys.length && keys.every((key) => key in value);
  const acceptLine = (line: string) => {
    if (!line.trim()) return;
    let raw: unknown;
    try { raw = JSON.parse(line); } catch { throw new Error("Answer stream contains malformed JSON"); }
    if (typeof raw !== "object" || raw === null || Array.isArray(raw)) throw new Error("Answer stream contains an invalid event");
    const record = raw as Record<string, unknown>;
    if (record.version !== 1 || typeof record.type !== "string") throw new Error("Answer stream contains an invalid event");
    let event: AnswerStreamEvent;
    if (record.type === "delta") {
      if (!exactKeys(record, ["version", "type", "text"]) || typeof record.text !== "string" || !record.text.length) throw new Error("Answer stream contains a malformed delta event");
      event = { version: 1, type: "delta", text: record.text };
    } else if (record.type === "citations") {
      if (!exactKeys(record, ["version", "type", "citations"]) || !Array.isArray(record.citations)) throw new Error("Answer stream contains malformed citations");
      const citations: Citation[] = record.citations.map((value): Citation => {
        if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("Answer stream contains a malformed citation");
        const citation = value as Record<string, unknown>;
        const optionalText = (item: unknown) => item === null || (typeof item === "string" && item.length > 0);
        if (!exactKeys(citation, ["citation_id", "subject", "course_id", "document_title", "section_title", "source_filename", "physical_page_number", "excerpt"]) || typeof citation.citation_id !== "string" || !citation.citation_id.length || typeof citation.subject !== "string" || !citation.subject.length || typeof citation.course_id !== "string" || !citation.course_id.length || !optionalText(citation.document_title) || !optionalText(citation.section_title) || typeof citation.source_filename !== "string" || !citation.source_filename.length || !Number.isInteger(citation.physical_page_number) || (citation.physical_page_number as number) < 1 || typeof citation.excerpt !== "string" || !citation.excerpt.length) throw new Error("Answer stream contains a malformed citation");
        return { citation_id: citation.citation_id, subject: citation.subject, course_id: citation.course_id, document_title: citation.document_title as string | null, section_title: citation.section_title as string | null, source_filename: citation.source_filename, physical_page_number: citation.physical_page_number as number, excerpt: citation.excerpt };
      });
      event = { version: 1, type: "citations", citations };
    } else if (record.type === "completed") {
      if (!exactKeys(record, ["version", "type"])) throw new Error("Answer stream contains a malformed completion event");
      event = { version: 1, type: "completed" };
    } else if (["clarification", "abstention", "error"].includes(record.type)) {
      if (!exactKeys(record, ["version", "type", "message"]) || typeof record.message !== "string" || !record.message.length) throw new Error("Answer stream contains a malformed message event");
      event = { version: 1, type: record.type as "clarification" | "abstention" | "error", message: record.message };
    } else {
      throw new Error("Answer stream contains an unknown event");
    }
    if (state.value === "terminal") throw new Error("Answer stream contains an event after termination");
    if (event.type === "delta") {
      if (state.value !== "deltas" || typeof event.text !== "string") throw new Error("Answer stream event order is invalid");
    } else if (event.type === "citations") {
      if (state.value !== "deltas" || !Array.isArray(event.citations)) throw new Error("Answer stream event order is invalid");
      state.value = "citations";
    } else if (event.type === "completed") {
      if (state.value !== "citations") throw new Error("Answer stream completed before citations");
      state.value = "terminal";
    } else {
      if (event.type === "error" && state.value !== "deltas") throw new Error("Answer stream event order is invalid");
      state.value = "terminal";
    }
    onEvent(event);
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      let newline = pending.indexOf("\n");
      while (newline >= 0) { acceptLine(pending.slice(0, newline)); pending = pending.slice(newline + 1); newline = pending.indexOf("\n"); }
      if (done) break;
    }
    if (pending.trim()) acceptLine(pending);
    if (state.value !== "terminal") throw new Error("Answer stream ended without a terminal event");
  } catch (error) {
    await reader.cancel(error).catch(() => undefined);
    throw error;
  } finally {
    reader.releaseLock();
  }
}

export async function requestAnswerStream(scope: AnswerScope, question: string, onEvent: (event: AnswerStreamEvent) => void, fetcher: typeof fetch = fetch): Promise<void> {
  const body = { ...(scope.subject ? { subject: scope.subject } : {}), ...(scope.courseId ? { course_id: scope.courseId } : {}), question };
  const response = await fetcher(`${apiBaseUrl}/api/v1/answers/stream`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body), cache: "no-store",
  });
  await consumeAnswerStream(response, onEvent);
}

export async function fetchReadiness(fetcher: typeof fetch = fetch): Promise<Readiness> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/readiness`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Readiness request failed with status ${response.status}`);
  }
  const body: unknown = await response.json();
  if (
    typeof body !== "object" ||
    body === null ||
    !("status" in body) ||
    body.status !== "ready" ||
    !("service" in body) ||
    body.service !== "course-rag-api"
  ) {
    throw new Error("Readiness response has an invalid shape");
  }
  return { status: body.status, service: body.service };
}

export async function fetchCourses(fetcher: typeof fetch = fetch): Promise<Course[]> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/courses`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Courses request failed with status ${response.status}`);
  const body: unknown = await response.json();
  if (typeof body !== "object" || body === null || !("courses" in body) || !Array.isArray(body.courses)) {
    throw new Error("Courses response has an invalid shape");
  }
  return body.courses.map((value: unknown): Course => {
    if (typeof value !== "object" || value === null) throw new Error("Courses response has an invalid shape");
    const course = value as Record<string, unknown>;
    if (typeof course.subject !== "string" || !course.subject.length || typeof course.course_id !== "string" || !course.course_id.length || !Number.isInteger(course.document_count) || !Number.isInteger(course.page_count)) {
      throw new Error("Courses response has an invalid shape");
    }
    return { subject: course.subject, course_id: course.course_id, document_count: course.document_count as number, page_count: course.page_count as number };
  });
}
