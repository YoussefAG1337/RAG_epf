---
type: epic
title: "Working RAG tracer"
parent: initiative-local-course-rag-assistant
covers: [CAP-1, CAP-2, CAP-3, CAP-4, CAP-7]
after: []
assignee: ""
risk: high
---

# Working RAG tracer

## Description

Deliver the first thin but real course RAG path: start the local stack, ingest one searchable PDF, retrieve only its course evidence, produce a claim-linked grounded answer, and stream it into a basic browser chat.

## Outcome

After five attended build sessions, a developer can demonstrate one PDF becoming a streamed, cited answer in the browser using deterministic provider doubles by default.

## Requirements

- CAP-1: Establish validated one-PDF ingestion with deterministic page-aware chunks; collection synchronization and idempotency are completed in Epic 7.
- CAP-2: Deliver the supported-answer happy path using only retrieved requested-course evidence; insufficiency and ambiguity safety are completed in Epic 7.
- CAP-3: Stream typed answer events into a basic browser chat; complete conversation and failure UX are delivered in Epic 7.
- CAP-4: Carry structured claim citation identifiers with filename, one-based physical page, and excerpt provenance; full citation cards are delivered in Epic 7.
- CAP-7: Establish course-scoped schema, ingestion, pre-ranking retrieval filtering, generation context, and citation resolution.
- T1: Use FastAPI, Next.js, PostgreSQL/pgvector via Docker Compose, SQLAlchemy, Alembic, Pydantic, pypdf, and direct Gemini integration without LangChain or LlamaIndex. (stack.md, Components)

## Done when

1. A clean local command starts PostgreSQL/pgvector, FastAPI, and Next.js with offline deterministic provider seams.
2. One searchable PDF is ingested for a course with deterministic chunks and physical-page provenance.
3. A wrong-course nearer vector is excluded before ranking and a supported answer uses only selected excerpts.
4. Every substantive answer claim resolves to same-course filename, page, and excerpt evidence.
5. The browser visibly receives nonduplicated answer deltas before exactly one terminal event.

## Boundaries

The first supported happy path across every layer. Multi-file synchronization, unchanged and changed-file behavior, insufficiency and ambiguity policy, complete student UX, and evaluation remain in later epics.

## References

- parent — _bmad-output/initiative-local-course-rag-assistant/initiative-local-course-rag-assistant.md
- spec — _bmad-output/specs/spec-local-course-rag-assistant/SPEC.md, CAP-1, CAP-2, CAP-3, CAP-4, CAP-7, and Constraints
- implementation contract — _bmad-output/specs/spec-local-course-rag-assistant/stack.md

## Notes

- Decision: tests, cleanup, and refactoring are part of each feature story; no routine epic refactor sweep is created, and final cross-initiative hardening is Story 8.3 (user direction, 2026-09-24).
- Decision: Story 6.1 owns configuration shapes and readiness only; Story 6.2 owns semantic runtime validation and the model-schema compatibility guard (validated 2026-09-24).
- Decision: Story 6.2 keeps provider adapters to thin direct interfaces plus deterministic doubles; orchestration remains later (validated 2026-09-24).
- Decision: Story 6.4 implements only the supported path; insufficiency, ambiguity, and abstention policy remain Story 7.3 (validated 2026-09-24).
- Decision: Story 6.5 defines clarification and abstention event types as a contract handoff even though behavior is completed in Story 7.3 (validated 2026-09-24).
- Decision: clean-schema inspection independently checks Story 6.2; cross-course isolation tests independently check Stories 6.3 and 6.4 (validated 2026-09-24).

