# Current Implementation Notes

This file records the architecture and behavior visible in the current source. It is descriptive; aspirational requirements belong in a future-state plan.

## Components

- **Web:** Next.js course upload, course selection, chat transcript, streamed event rendering, and citation display.
- **API:** FastAPI readiness, course listing, PDF upload/ingestion, and NDJSON answer streaming.
- **Database:** PostgreSQL with pgvector, SQLAlchemy, and Alembic migrations.
- **PDF processing:** `pypdf` text extraction and deterministic per-page chunking with overlap.
- **Providers:** Deterministic embedding/generation for offline use; Gemini embeddings and generation; OpenAI and Groq answer generation. Provider choice and API keys are configured on the server.
- **Validation:** Pydantic models validate runtime settings, API inputs, provider output, and stream events.

## Persisted records

- A document stores a UUID, course ID, source filename, SHA-256 checksum, page count, embedding provider, and creation time.
- A chunk stores a UUID, document and course IDs, one-based physical page number, deterministic position, extracted text, and a 768-dimensional vector.
- A schema metadata row records the configured embedding model and dimensions.
- Course names are represented by `Document.course_id`; there is no standalone course table or additional course metadata.
- Conversation messages are not persisted in the database.

## Ingestion behavior

- The web upload accepts one PDF and a new unique course name. The request body is capped at 25 MiB. The upload is stored beneath `PDF_SOURCE_DIR/.uploads/` and ingested during the same API request.
- A course with an existing document is rejected by the web upload endpoint. The UI therefore supports one uploaded PDF per course.
- The CLI ingests one PDF selected beneath the configured source directory for a supplied course ID.
- Ingestion rejects invalid, encrypted, empty, or non-searchable PDFs. It extracts text per physical page and does not perform OCR.
- Checksums and deterministic document/chunk identifiers make an unchanged source a no-op when its embedding provider matches.
- If the provider differs, the current code re-embeds the source and updates vectors transactionally when persisted chunks match. General changed-source reconciliation and stale document cleanup are not implemented.

## Answer flow

1. Validate the selected course ID, question, and bounded history turns.
2. Embed the question, incorporating recent history to resolve follow-up references.
3. Filter chunks to the selected course in SQL, rank by cosine distance, limit results, and discard results below the configured score threshold.
4. Ask the selected generation provider for JSON claims grounded in retrieved excerpts. Conversation history is labeled as untrusted contextual information.
5. Validate response structure and ensure every citation ID resolves to retrieved evidence. This validates citation membership, not semantic support of each claim.
6. Emit claim text as bounded NDJSON delta events, followed by citation metadata and completion. Text is emitted after generation completes; this is not live token streaming.
7. Convert failures, including insufficient evidence, to a generic safe error event. Dedicated abstention and clarification event variants are not implemented in the active answer flow.

## API and browser behavior

- `GET /api/v1/readiness` reports API readiness.
- `GET /api/v1/courses` lists courses derived from stored documents and their source filenames.
- `POST /api/v1/courses?course_id=...&filename=...` accepts the raw PDF body and creates a course from one searchable PDF.
- `POST /api/v1/answers/stream` accepts `course_id`, `question`, and up to 12 `{role, content}` history turns. It returns newline-delimited JSON.
- The browser retains chat history for the current page session. New chat, course change, and reload clear it. Only the selected course's retrieved excerpts are factual grounding input for each response.

## Runtime configuration

- Local defaults use deterministic providers. `RAG_PROVIDER` selects the embedding provider; `ANSWER_PROVIDER` can independently select deterministic, Gemini, OpenAI, or Groq answer generation. When unset, answer provider follows `RAG_PROVIDER`.
- Embedding model/dimensions are fixed to `gemini-embedding-2` and 768 by configuration and database schema checks.
- Retrieval count and minimum cosine similarity score are configurable.
- Database connection, PDF source directory, frontend origin, provider credentials, and provider models are server-side settings.
- Docker Compose is the documented local topology: database, API, and web.

## Known verification boundary

Unit and frontend tests are present in the repository, but this companion does not claim that they pass. No integration evaluation runner or evaluation dataset is currently wired into the application. The intended evaluation categories and manual review approach are described separately in `evaluation.md` and should be treated as planned until implemented.
