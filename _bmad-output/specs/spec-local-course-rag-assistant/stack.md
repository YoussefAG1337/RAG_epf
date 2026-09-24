# Implementation Contract

## Components

| Area | Required choice | Responsibility |
|---|---|---|
| Backend | Python and FastAPI | HTTP API, retrieval orchestration, streaming, ingestion command integration, and evaluation command integration |
| Frontend | Next.js | Single-course student chat, streamed response rendering, abstention states, and citation cards |
| Database | PostgreSQL with pgvector via Docker Compose | Documents, chunks, vector similarity search, ingestion state, and schema migrations |
| Model provider | Gemini API | `gemini-embedding-2` embeddings at 768 dimensions and `gemini-3.8-flash` grounded answer generation |
| PDF extraction | `pypdf` | Text extraction with physical PDF page provenance |
| Persistence | SQLAlchemy and Alembic | Data access, transactions, vector queries, and migrations |
| Validation | Pydantic | Configuration, command inputs, API requests, streamed events, citations, and evaluation records |

Direct integrations are required. LangChain and LlamaIndex must not be introduced.

## Data invariants

- A document record contains at least: stable identifier, `course_id`, source filename, checksum, page count, and ingestion timestamps.
- A chunk record contains at least: stable identifier, document identifier, `course_id`, one-based physical PDF page number, extracted text, 768-dimensional embedding, and deterministic position within the document.
- The document checksum is derived from source file bytes. An unchanged `(course_id, source identity, checksum)` is a no-op on re-ingestion.
- Replacing a changed source must atomically replace or reconcile its derived chunks so stale and current versions are never retrieved together.
- Database constraints and indexes enforce course scoping, document/chunk relationships, idempotency keys, and vector retrieval needs.
- Retrieved evidence retains chunk, document, filename, page, and excerpt provenance through generation and serialization.
- The embedding column schema matches the configured 768 dimensions. The embedding model identifier and dimensionality are coupled schema metadata; changing either requires an Alembic migration appropriate to the new vector schema and complete re-embedding of all chunks before retrieval resumes.

## Ingestion behavior

- A local command accepts the course identifier and PDF source directory through validated arguments or configuration.
- It rejects unreadable, encrypted, non-PDF, scanned, or text-empty inputs with a per-file diagnostic; one bad file must not silently corrupt the collection.
- Chunk identifiers and ordering are deterministic for unchanged extracted content.
- Embeddings are created only for new or changed chunks.
- A run reports inserted, updated, unchanged, skipped, and failed files and exits nonzero when ingestion cannot establish a consistent searchable collection.

## Question-answer flow

1. Validate the question and `course_id`.
2. Embed the question with `gemini-embedding-2` at 768 output dimensions.
3. Retrieve and rank only chunks matching `course_id`.
4. Decide whether the evidence is sufficient; ambiguous questions may require an abstention or clarification request rather than a guessed interpretation.
5. Send only the question, strict grounding instructions, and selected excerpts with stable citation identifiers to `gemini-3.8-flash` by default.
6. Stream typed events for answer deltas, final citations, completion, and errors.
7. Validate that final citation identifiers resolve to retrieved evidence before exposing a supported answer. If grounding validation fails, return an abstention instead.

## API and UI contract

- The backend exposes a versioned chat endpoint with a transport suitable for one-way answer streaming from a single request.
- The stream distinguishes text deltas, final citation metadata, completion, abstention, and error states.
- A citation contains a stable citation identifier, source filename, one-based physical PDF page number, and supporting excerpt.
- Printed page labels are neither extracted nor displayed in V1.
- The UI prevents accidental duplicate submissions, indicates streaming progress, preserves the current in-memory conversation, and renders failures without fabricating a partial completion.
- Conversation persistence across browser reloads is not required unless added by a later spec update.

## Configuration and secrets

- Runtime configuration is validated at startup and includes the database URL, Gemini API key, answer model defaulting to `gemini-3.8-flash`, embedding model fixed to `gemini-embedding-2`, embedding dimensions fixed to 768, retrieval limits, and evidence sufficiency settings.
- Startup must reject embedding configuration that does not match the migrated database schema and stored embedding metadata.
- Secrets are supplied through local environment configuration and are never committed or returned to the browser.
- One Docker Compose definition runs PostgreSQL with pgvector for local development; application processes may run locally outside containers.

## Verification boundaries

- Unit tests cover checksum/idempotency logic, chunk provenance, course filtering, citation resolution, sufficiency decisions, and stream serialization.
- Integration tests cover migrations, pgvector retrieval, changed-file re-ingestion, API streaming, and the Gemini boundary using controlled test doubles.
- No test may require live Gemini access by default; an opt-in smoke test may verify provider compatibility.
