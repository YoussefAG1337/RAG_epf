---
type: initiative
title: "Local Course RAG Assistant"
parent: none
covers: [CAP-1, CAP-2, CAP-3, CAP-4, CAP-5, CAP-6, CAP-7, CAP-8]
after: []
assignee: ""
risk: high
---

# Local Course RAG Assistant

## Description

Build the locally runnable assistant defined by the canonical specification: ingest a fixed course PDF collection, answer only from course evidence, stream claim-linked citations in chat, abstain safely, and evaluate the behavior repeatably.

## Outcome

The specification's success signal is met locally for a fixed searchable course corpus, including supported and unsupported evaluation cases.

## Done when

1. A clean local setup ingests the fixed course PDF collection twice without duplicating unchanged documents, chunks, or embeddings.
2. A supported question streams a grounded answer whose substantive claims resolve to displayed filename, physical-page, and excerpt evidence from the requested course.
3. An ambiguous or unsupported question produces clarification or materials-insufficient abstention without model-prior facts or misleading citations.
4. The deterministic evaluation run passes every structural check and exposes every semantic result for attended, case-level acceptance.
5. Course isolation is enforced in stored documents, chunks, retrieval, generation context, and evaluation evidence.

## Boundaries

Three vertical outcome boundaries: a working RAG tracer, reliability and student experience, and evaluation with final hardening. The non-goals remain excluded. Tracer path: after five stories, ingest one course PDF and stream one supported answer with a basic cited browser response; later stories complete synchronization, safety, evidence inspection, and evaluation.

- Touch point: local PDF filesystem — read-only configured source collection; owners: epic-working-rag-tracer and epic-reliability-student-experience
- Touch point: PostgreSQL/pgvector — local Compose service, schema, and course-scoped retrieval; owner: epic-working-rag-tracer
- Touch point: Gemini API — direct provider boundary, grounded generation, and optional evaluation judge; owners: epic-working-rag-tracer and epic-evaluation-hardening
- Touch point: browser — basic tracer chat completed by the student experience; owners: epic-working-rag-tracer and epic-reliability-student-experience

## References

- spec — _bmad-output/specs/spec-local-course-rag-assistant/SPEC.md, Capabilities, Constraints, Non-goals, and Success signal
- implementation contract — _bmad-output/specs/spec-local-course-rag-assistant/stack.md
- evaluation contract — _bmad-output/specs/spec-local-course-rag-assistant/evaluation.md

## Notes

- Decision: replace the original five-epic, 33-story plan with three vertical epics and twelve attended-session-sized stories; the two stories beyond the requested approximation keep changed-file replacement and evaluation checks safely bounded (approved 2026-09-24).
- Decision: the first controlled end-to-end RAG demonstration is complete after Story 6.5 (approved 2026-09-24).
- Decision: feature stories include relevant tests, cleanup, and refactoring; only Story 8.3 is a standalone cross-initiative integration and hardening pass (approved 2026-09-24).
- Decision: valid files commit independently; a failed requested source retains its previous valid version when available, is reported, and makes the synchronization run nonzero (approved 2026-09-24).
- Decision: accept partially text-bearing PDFs page by page and diagnose empty pages; reject a PDF as scanned or text-empty only when it has no usable searchable text (approved 2026-09-24).
- Decision: CAP-8 completion requires the deterministic double-backed run and attended case-level semantic review; live Gemini evaluation is optional and explicitly enabled (approved 2026-09-24).
