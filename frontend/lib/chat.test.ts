import { describe, expect, it } from "vitest";
import type { Citation } from "./api";
import { applyEvent, citationTarget, fromStored, pendingAnswer, scopeLabel } from "./chat";

const citation: Citation = {
  number: 2,
  chunk_id: "c",
  document_id: "d1",
  subject: "Général",
  course_id: "stat",
  document_title: null,
  section_title: null,
  source_filename: "stat/fiche.md",
  page: 1,
  excerpt: "",
  quotes: [],
  highlights: [{ page: 1, boxes: [], lines: [12, 13] }],
};

describe("chat state", () => {
  it("builds an answer from stream events", () => {
    let answer = pendingAnswer("a1");
    answer = applyEvent(answer, { version: 2, type: "status", stage: "writing", retrieval_query: "Q réécrite" });
    answer = applyEvent(answer, { version: 2, type: "claim", index: 0, text: "Première.", citations: [1] });
    answer = applyEvent(answer, { version: 2, type: "citations", citations: [citation] });
    answer = applyEvent(answer, { version: 2, type: "completed", message_id: "m" });
    expect(answer).toMatchObject({ status: "answered", stage: null, retrievalQuery: "Q réécrite", claims: [{ text: "Première.", citations: [1] }], citations: [citation] });
  });

  it("turns an abstention into a notice without partial claims", () => {
    let answer = applyEvent(pendingAnswer("a1"), { version: 2, type: "claim", index: 0, text: "x", citations: [1] });
    answer = applyEvent(answer, { version: 2, type: "abstention", message: "Pas trouvé." });
    expect(answer).toMatchObject({ status: "abstained", claims: [], message: "Pas trouvé." });
  });

  it("restores saved messages and only shows rewritten searches", () => {
    const messages = fromStored([
      { id: "u1", role: "user", content: "Et AES ?", payload: {}, created_at: "" },
      { id: "a1", role: "assistant", content: "AES…", payload: { status: "answered", claims: [{ text: "AES…", citations: [2] }], citations: [citation], retrieval_query: "Comment fonctionne AES ?" }, created_at: "" },
      { id: "u2", role: "user", content: "Coupe du monde ?", payload: {}, created_at: "" },
      { id: "a2", role: "assistant", content: "Pas trouvé.", payload: { status: "abstained", retrieval_query: "Coupe du monde ?" }, created_at: "" },
    ]);
    expect(messages[1]).toMatchObject({ status: "answered", retrievalQuery: "Comment fonctionne AES ?" });
    expect(messages[3]).toMatchObject({ status: "abstained", message: "Pas trouvé.", retrievalQuery: null });
  });

  it("opens a citation at its highlighted page", () => {
    expect(citationTarget(citation, 7)).toEqual({ documentId: "d1", page: 1, highlights: citation.highlights, citationNumber: 2, nonce: 7 });
  });

  it("labels scopes in French", () => {
    const documents = [{ id: "d1", subject: "Général", course_id: "stat", title: "Fiche", source_filename: "stat/fiche.md", page_count: 1 }];
    expect(scopeLabel({}, documents)).toBe("Tous les cours");
    expect(scopeLabel({ course_id: "stat" }, documents)).toBe("stat");
    expect(scopeLabel({ subject: "Maths" }, documents)).toBe("Toute la matière Maths");
    expect(scopeLabel({ document_id: "d1" }, documents)).toBe("Fiche");
  });
});
