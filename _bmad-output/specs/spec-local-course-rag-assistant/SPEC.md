---
id: SPEC-local-course-rag-assistant
companions:
  - stack.md
  - evaluation.md
sources:
  - README.md
  - backend/app/main.py
  - backend/app/answering.py
  - backend/app/ingestion.py
  - frontend/app/page.tsx
---

> **Current-state contract.** This document describes the implemented local application. It is a snapshot of current behavior, not a promise that every desirable future capability exists.

# Local Course RAG Assistant

## Purpose

The application lets a local user upload text-searchable PDF course material, select an ingested course, and ask questions grounded in excerpts retrieved from that course. Answers include source filenames, physical PDF page numbers, and excerpts. Recent in-memory chat turns help interpret follow-up questions.

## Current capabilities

- **CAP-1 — Add a course from a PDF:** The web UI accepts a course name and one PDF. The API limits uploads to 25 MB, extracts searchable text, chunks text without crossing physical page boundaries, creates embeddings, and persists the document and chunks. A course name must be unique; each upload creates exactly one course.
- **CAP-2 — List and select courses:** The UI loads available courses from the API and displays their source filenames. A user selects a course before asking questions. The API's course list is derived from persisted documents; there is no separate course metadata record.
- **CAP-3 — Ask course-scoped questions:** Questions are embedded and matched against chunks filtered to the selected course before similarity ranking. The answer model receives retrieved excerpts and bounded conversation history. Conversation history is contextual only and is not treated as factual evidence by the prompt.
- **CAP-4 — Cite supporting excerpts:** Generated answers are structured as claims with citation IDs. The backend rejects malformed claims and citation IDs that were not among the retrieved evidence. Returned citation details include filename, one-based physical PDF page, and excerpt.
- **CAP-5 — Continue an in-memory chat:** The browser retains completed user and assistant turns for the current page session and sends up to 12 turns with a follow-up question. Starting a new chat, switching courses, or reloading clears that local history. No server-side conversation persistence exists.
- **CAP-6 — Receive an incremental response display:** The API emits newline-delimited JSON events and the UI renders response deltas, citations, and a terminal completion or safe error event. The answer is generated before its text is divided into deltas; model-token streaming is not implemented.
- **CAP-7 — Ingest from the local command line:** A developer can ingest one selected PDF for a supplied course ID from the configured PDF source directory. Unchanged files with the same embedding provider are no-ops. Provider changes can replace vectors transactionally when stored chunks match the source.
- **CAP-8 — Run with configurable model providers:** Deterministic providers support offline development. Gemini can provide embeddings and generation; OpenAI and Groq are supported for answer generation. Embeddings use a fixed 768-dimensional schema. Provider configuration and API keys remain server-side.
- **CAP-9 — Run as a local Compose application:** Docker Compose starts the PostgreSQL/pgvector database, FastAPI service, and Next.js web interface. The API exposes readiness, course-list, course-upload, and streaming-answer endpoints.

## Current limits

- Only text-searchable PDFs are supported. Scanned PDFs requiring OCR, audio, and video are unsupported.
- The web upload flow creates one new course per PDF. It does not add documents to an existing course, or rename or delete courses or documents.
- Uploaded PDF size is limited to 25 MB. Upload processing and ingestion happen in the API request; a background job queue and durable progress tracking are not implemented.
- Conversation history is browser memory only. There are no user accounts, authentication, authorization, or per-user data boundaries.
- A citation is checked to ensure its ID belongs to retrieved evidence; the system does not independently verify that the excerpt entails the generated claim.
- When no sufficiently relevant evidence is found, the answering layer raises an error that is returned through the stream's generic safe error event. Dedicated abstention and clarification events are reserved but not implemented.
- The response is displayed in pieces only after generation finishes; provider-level live token streaming is not implemented.
- The application is configured for local development. Production deployment, monitoring, backups, and operational controls are outside current scope.
- The evaluation contract in `evaluation.md` describes intended evaluation coverage; an evaluation runner and dataset are not part of the current app behavior.

## Data and grounding rules

- Documents and chunks carry a course ID. Every retrieval query filters by that ID before ranking and limiting results.
- Chunks retain document, source filename, physical page, position, text, and embedding provenance.
- Physical page numbering is one-based. Printed page labels are not extracted or displayed.
- The prompt asks the generation provider to use only retrieved excerpts for factual claims. The backend validates response structure and citation membership, not semantic entailment.
- Embedding model and vector dimensions are fixed by the migrated schema at `gemini-embedding-2` and 768 dimensions. The deterministic provider remains available for local development while using the same vector shape.
- PDF files are stored beneath the configured `PDF_SOURCE_DIR`; the database stores document metadata, chunks, and vectors.

## Explicitly out of scope today

- Authentication, accounts, multi-user ownership, or hosted production deployment.
- Persistent chat transcripts or cross-device conversations.
- Adding multiple PDFs to an existing course and course/document lifecycle management.
- OCR and non-PDF learning materials.
- Cross-course retrieval.
- Semantic claim-entailment verification, a dedicated abstention/clarification event flow, and a completed evaluation runner.

