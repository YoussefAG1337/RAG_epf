import { describe, expect, it, vi } from "vitest";
import { type AnswerStreamEvent, consumeAnswerStream, fetchReadiness, requestAnswerStream } from "./api";

const citation = {
  number: 1,
  chunk_id: "c1",
  document_id: "d1",
  subject: "Général",
  course_id: "Cryptographie",
  document_title: "Introduction à la cryptologie",
  section_title: "RSA",
  source_filename: "Cryptographie/slides.pdf",
  page: 52,
  excerpt: "Bob choisit deux grands nombres premiers.",
  quotes: ["deux grands nombres premiers"],
  highlights: [{ page: 52, boxes: [[0.1, 0.2, 0.6, 0.25]], lines: [] }],
};

const ndjson = (...events: object[]) => new Response(events.map((event) => JSON.stringify({ version: 2, ...event })).join("\n") + "\n");

async function collect(response: Response): Promise<AnswerStreamEvent[]> {
  const events: AnswerStreamEvent[] = [];
  await consumeAnswerStream(response, (event) => events.push(event));
  return events;
}

describe("answer stream", () => {
  it("delivers a complete answer in order", async () => {
    const events = await collect(
      ndjson(
        { type: "conversation", conversation_id: "conv" },
        { type: "status", stage: "searching", retrieval_query: null },
        { type: "status", stage: "writing", retrieval_query: "Comment fonctionne AES ?" },
        { type: "claim", index: 0, text: "RSA utilise deux premiers.", citations: [1] },
        { type: "citations", citations: [citation] },
        { type: "completed", message_id: "m1" },
      ),
    );
    expect(events.map((event) => event.type)).toEqual(["conversation", "status", "status", "claim", "citations", "completed"]);
    expect(events[2]).toMatchObject({ stage: "writing", retrieval_query: "Comment fonctionne AES ?" });
    expect(events[4]).toMatchObject({ citations: [citation] });
  });

  it("accepts an abstention as the end of the stream", async () => {
    const events = await collect(ndjson({ type: "conversation", conversation_id: "c" }, { type: "abstention", message: "Pas trouvé." }));
    expect(events.at(-1)).toEqual({ version: 2, type: "abstention", message: "Pas trouvé." });
  });

  it.each([
    ["an unknown event", ndjson({ type: "surprise" })],
    ["a malformed claim", ndjson({ type: "claim", index: 0, text: "x", citations: ["1"] })],
    ["the old contract version", new Response('{"version":1,"type":"completed"}\n')],
    ["an event after the end", ndjson({ type: "completed", message_id: null }, { type: "claim", index: 0, text: "x", citations: [] })],
    ["a stream that stops early", ndjson({ type: "claim", index: 0, text: "x", citations: [] })],
  ])("rejects %s", async (_label, response) => {
    await expect(collect(response)).rejects.toThrow();
  });

  it("sends the question, conversation, and scope", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ndjson({ type: "completed", message_id: null }));
    await requestAnswerStream({ question: "Et AES ?", conversation_id: "conv", scope: { document_id: "d1" } }, () => undefined, { fetcher });
    expect(fetcher).toHaveBeenCalledWith(
      "http://localhost:8000/api/v1/answers/stream",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ question: "Et AES ?", conversation_id: "conv", scope: { document_id: "d1" } }) }),
    );
  });
});

describe("fetchReadiness", () => {
  it("validates the readiness response", async () => {
    const ok = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ status: "ready", service: "course-rag-api" })));
    await expect(fetchReadiness(ok)).resolves.toEqual({ status: "ready", service: "course-rag-api" });
    const wrong = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ status: "ready", service: "other" })));
    await expect(fetchReadiness(wrong)).rejects.toThrow("invalid shape");
  });
});
