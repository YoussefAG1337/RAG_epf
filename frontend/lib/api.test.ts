import { describe, expect, it, vi } from "vitest";

import { consumeAnswerStream, fetchCourses, fetchReadiness, requestAnswerStream } from "./api";

describe("fetchReadiness", () => {
  it("requests the readiness endpoint without caching", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ status: "ready", service: "course-rag-api" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await fetchReadiness(fetcher);

    expect(fetcher).toHaveBeenCalledWith("http://localhost:8000/api/v1/readiness", {
      cache: "no-store",
    });
  });

  it("returns a valid readiness response", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ status: "ready", service: "course-rag-api" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(fetchReadiness(fetcher)).resolves.toEqual({
      status: "ready",
      service: "course-rag-api",
    });
  });

  it("rejects a malformed successful response", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ status: "ready", service: "other" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(fetchReadiness(fetcher)).rejects.toThrow(
      "Readiness response has an invalid shape",
    );
  });

  it("rejects a non-OK response", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 503 }));

    await expect(fetchReadiness(fetcher)).rejects.toThrow(
      "Readiness request failed with status 503",
    );
  });
});

describe("consumeAnswerStream", () => {
  it("parses frames split across arbitrary network chunks in order", async () => {
    const bytes = new TextEncoder().encode('{"version":1,"type":"delta","text":"Hi"}\n{"version":1,"type":"citations","citations":[]}\n{"version":1,"type":"completed"}\n');
    const response = new Response(new ReadableStream({ start(controller) { controller.enqueue(bytes.slice(0, 17)); controller.enqueue(bytes.slice(17, 53)); controller.enqueue(bytes.slice(53)); controller.close(); } }));
    const events: string[] = [];
    await consumeAnswerStream(response, (event) => events.push(event.type));
    expect(events).toEqual(["delta", "citations", "completed"]);
  });

  it("rejects duplicate terminal and post-terminal events", async () => {
    const response = new Response('{"version":1,"type":"error","message":"safe"}\n{"version":1,"type":"completed"}\n');
    await expect(consumeAnswerStream(response, () => undefined)).rejects.toThrow("after termination");
  });

  it("rejects a citation with malformed provenance before invoking the callback", async () => {
    const response = new Response('{"version":1,"type":"citations","citations":[{"citation_id":"a","subject":"Info","course_id":"course-1","document_title":null,"section_title":null,"source_filename":"week.pdf","physical_page_number":"2","excerpt":"Evidence"}]}\n');
    const onEvent = vi.fn();
    await expect(consumeAnswerStream(response, onEvent)).rejects.toThrow("malformed citation");
    expect(onEvent).not.toHaveBeenCalled();
  });
});


describe("requestAnswerStream", () => {
  it("posts the course question and delivers events from the response stream", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(
      '{"version":1,"type":"delta","text":"Hi"}\n{"version":1,"type":"citations","citations":[]}\n{"version":1,"type":"completed"}\n',
      { status: 200, headers: { "Content-Type": "application/x-ndjson" } },
    ));
    const events: string[] = [];
    await requestAnswerStream({ courseId: "course-1" }, "Question?", (event) => events.push(event.type), fetcher);
    expect(fetcher).toHaveBeenCalledWith("http://localhost:8000/api/v1/answers/stream", expect.objectContaining({
      method: "POST", body: JSON.stringify({ course_id: "course-1", question: "Question?" }),
    }));
    expect(events).toEqual(["delta", "citations", "completed"]);
  });
});

describe("all-course questions", () => {
  it("omits course_id when asking across all courses", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(
      '{"version":1,"type":"abstention","message":"Not found"}\n',
      { status: 200, headers: { "Content-Type": "application/x-ndjson" } },
    ));
    const events: string[] = [];
    await requestAnswerStream({}, "Question?", (event) => events.push(event.type), fetcher);
    expect(fetcher).toHaveBeenCalledWith("http://localhost:8000/api/v1/answers/stream", expect.objectContaining({
      body: JSON.stringify({ question: "Question?" }),
    }));
    expect(events).toEqual(["abstention"]);
  });

  it("sends a subject scope", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('{"version":1,"type":"abstention","message":"Not found"}\n', { status: 200 }));
    await requestAnswerStream({ subject: "Informatique" }, "Question?", () => undefined, fetcher);
    expect(fetcher).toHaveBeenCalledWith("http://localhost:8000/api/v1/answers/stream", expect.objectContaining({
      body: JSON.stringify({ subject: "Informatique", question: "Question?" }),
    }));
  });

  it("accepts citations that name their course and rejects ones that do not", async () => {
    const valid = '{"version":1,"type":"citations","citations":[{"citation_id":"a","subject":"Info","course_id":"course-1","document_title":"Cours 1","section_title":null,"source_filename":"week.pdf","physical_page_number":2,"excerpt":"Evidence"}]}\n{"version":1,"type":"completed"}\n';
    const events: unknown[] = [];
    await consumeAnswerStream(new Response(valid), (event) => events.push(event));
    expect(events[0]).toEqual({ version: 1, type: "citations", citations: [{ citation_id: "a", subject: "Info", course_id: "course-1", document_title: "Cours 1", section_title: null, source_filename: "week.pdf", physical_page_number: 2, excerpt: "Evidence" }] });

    const missingCourse = '{"version":1,"type":"citations","citations":[{"citation_id":"a","subject":"Info","document_title":null,"section_title":null,"source_filename":"week.pdf","physical_page_number":2,"excerpt":"Evidence"}]}\n';
    await expect(consumeAnswerStream(new Response(missingCourse), () => undefined)).rejects.toThrow("malformed citation");
  });
});

describe("fetchCourses", () => {
  it("returns the validated course list", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(
      JSON.stringify({ courses: [{ subject: "Informatique", course_id: "algo-101", document_count: 2, page_count: 40 }] }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    await expect(fetchCourses(fetcher)).resolves.toEqual([{ subject: "Informatique", course_id: "algo-101", document_count: 2, page_count: 40 }]);
    expect(fetcher).toHaveBeenCalledWith("http://localhost:8000/api/v1/courses", { cache: "no-store" });
  });

  it("rejects a malformed course list", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ courses: [{ course_id: 3 }] }), { status: 200 }));
    await expect(fetchCourses(fetcher)).rejects.toThrow("invalid shape");
  });
});
