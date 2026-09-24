# Epic 6 Context: Working RAG tracer

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Deliver the first working end-to-end course RAG path: start the local stack, ingest one searchable PDF, retrieve only evidence from its course, produce a grounded answer with resolvable citations, and stream it into a basic browser chat. This gives developers a demonstrable, auditable happy path using deterministic provider doubles by default. The designated planning-artifacts directory is missing, so this context is compiled from the epic, initiative, canonical specification, and implementation contract.

## Stories

- Story 6.1: Application and test scaffold
- Story 6.2: Persistence and provider foundation
- Story 6.3: One-PDF page-aware ingestion
- Story 6.4: Course-filtered grounded cited answer
- Story 6.5: Streaming endpoint and basic browser chat

## Requirements & Constraints

- Support one searchable, text-based PDF through a local ingestion path; chunks must be deterministic and retain one-based physical PDF page provenance.
- Scope documents, chunks, retrieval, generation context, and citation resolution to the requested course. Apply the course filter before vector ranking.
- Ground supported answers only in selected excerpts from that course. Validate citation identifiers against retrieved evidence; each citation must resolve to filename, physical page, and supporting excerpt.
- Stream answer text before a single terminal completion event, without duplicated deltas or events after termination. Keep insufficiency and ambiguity behavior, full student UX, and synchronization/idempotency beyond the initial path for later epics.
- Use deterministic provider doubles by default; routine tests must not require live Gemini access. Keep secrets server-side.

## Technical Decisions

- Use Python/FastAPI, Next.js, PostgreSQL with pgvector through Docker Compose, SQLAlchemy, Alembic, Pydantic, and pypdf. Integrate directly with Gemini; do not add LangChain or LlamaIndex.
- The answer model defaults to `gemini-3.8-flash`. Embeddings use `gemini-embedding-2` at 768 dimensions. Model and dimensionality are coupled to the vector schema; startup must reject configuration incompatible with schema metadata.
- Persist course identity and source/page provenance with documents and chunks. Preserve retrieved evidence provenance through generation and stream serialization.
- Keep provider adapters as thin direct interfaces with deterministic doubles; orchestration is owned by the answer-path story.

## UX & Interaction Patterns

- Provide a basic single-course browser chat with a prompt, incremental answer display, and visible citation metadata. The backend stream distinguishes deltas, citations, completion, clarification, abstention, and errors.
- Prevent duplicate submissions and show streaming progress. Full citation cards, complete failure UX, and clarification or abstention decision behavior belong to later work.

## Cross-Story Dependencies

- Stories proceed in order: scaffold (6.1) → persistence and provider foundation (6.2) → ingestion (6.3) → grounded cited answer (6.4) → streaming chat (6.5).
- This epic establishes only the supported happy path. Collection synchronization and idempotency, insufficient/ambiguous evidence policy, and completed student experience are owned by later epics.
