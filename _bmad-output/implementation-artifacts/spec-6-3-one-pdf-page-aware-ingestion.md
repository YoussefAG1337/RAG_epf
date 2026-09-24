---
title: 'One-PDF page-aware ingestion'
type: 'feature'
created: '2026-09-24'
status: 'done'
baseline_commit: '6d84515d8a141ba08ec526c6cea1102d63af2538'
route: 'full'
route_source: 'auto'
review: 'thorough'
review_source: 'auto'
lenses_ran: ['blind-hunter', 'edge-case-hunter', 'verification-gap', 'intent-alignment']
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-6-context.md'
  - '{project-root}/_bmad-output/specs/spec-local-course-rag-assistant/SPEC.md'
  - '{project-root}/_bmad-output/specs/spec-local-course-rag-assistant/stack.md'
  - '{project-root}/_bmad-output/initiative-local-course-rag-assistant/epic-working-rag-tracer/story-one-pdf-page-aware-ingestion.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The application has course-scoped document and vector-chunk storage and embedding providers, but no operator path that turns one selected PDF into searchable, page-linked records. Without that path, the first course cannot be ingested for the downstream answer story.

**Approach:** Add a local command that accepts a course ID and one PDF selected from a configured source directory (default `data/course-pdfs/`). Extract usable text page by page, create deterministic page-aware chunks of at most 1,000 characters with 150 characters of overlap, embed through the existing provider seam, and persist the document and chunks as one course-scoped operation.

## Boundaries & Constraints

**Always:** Use pypdf and the existing SQLAlchemy models, database engine and embedding-provider contracts. Resolve and validate the selected file beneath the configured source directory, and never scan neighboring files. Preserve one-based physical page provenance, stable chunk order and IDs, source checksum, course ID, and vector(768). Keep chunks within one page; target 1,000 characters with 150 characters of overlap, splitting at paragraph/sentence boundaries and hard-splitting only long spans. Use deterministic provider doubles by default in offline development and tests. Diagnose pages with no usable text; accept a PDF with at least one usable page and reject one with none. Persist a document and all its chunks atomically. Follow Epic 6's explicit deferral of unchanged-input idempotency and collection synchronization to Story 7.1.

**Never:** Add OCR, directory-wide ingestion, retrieval or answer orchestration, API/UI ingestion flows, changed-file replacement, synchronization, or unchanged-rerun handling. Do not add LangChain or LlamaIndex, call Gemini in ordinary tests, or expose provider credentials.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Mixed text PDF | Selected PDF has text on some pages and blank/scanned pages | Persist chunks from usable pages with physical page numbers; report each empty page | Empty pages are diagnostic warnings, not a whole-file failure |
| No searchable text | Valid PDF yields no usable text on any page | No document or chunks are committed | Report a clear error and exit nonzero |
| Unsafe or invalid source | Path escapes the configured root, including through a symlink; file is unreadable, encrypted, or not a PDF | No database writes occur | Report the source-specific failure and exit nonzero |

</frozen-after-approval>

## Code Map

- `backend/app/config.py` — validated `Settings`; add the configured PDF source root while preserving current defaults and secret handling.
- `backend/app/db.py` — `create_database_engine`; add/reuse session construction without opening connections at import time.
- `backend/app/models.py` — existing `Document` and `DocumentChunk` fields, course constraints, checksums, provenance, and vector(768); no schema change is indicated.
- `backend/app/providers/protocols.py` — `EmbeddingProvider.embed`; `backend/app/providers/deterministic.py` and `gemini.py` provide offline and direct live adapters.
- `backend/alembic/versions/20260924_0001_persistence_foundation.py` — existing persisted schema and course/source uniqueness; preserve the current migration contract.
- `backend/tests/` — offline pytest suite; add focused extraction, chunking, path validation, provider injection, and persistence-boundary coverage.
- `backend/pyproject.toml` — pypdf and database dependencies are already present; no dependency change is expected.
- `README.md` and `.env.example` — document source-root configuration and the one-file command.
- `_bmad-output/implementation-artifacts/spec-6-2-persistence-and-provider-foundation.md` — completed predecessor; reuse its schema and provider decisions and keep its deferred ingestion scope intact.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/config.py` and `.env.example` — define a configurable source directory with a local default so the command has one explicit containment boundary.
- [x] `backend/app/ingestion.py` — implement safe single-file resolution, PDF validation, page extraction and diagnostics, deterministic page-local chunking and IDs, controlled embedding, and atomic persistence using existing models.
- [x] `backend/app/db.py` and `backend/app/ingest.py` — provide a transaction/session boundary and validated CLI inputs for course ID and one selected PDF; keep provider choice injectable and deterministic by default.
- [x] `backend/tests/test_ingestion.py` — cover mixed/empty pages, no-text and invalid PDFs, path traversal and escaping symlinks, stable chunk order/IDs, 768-dimensional vectors, atomic failure, and same-source course isolation.
- [x] `README.md` — document configuring the source directory, running one-file ingestion, selecting the live provider when needed, and inspecting the resulting rows.

**Acceptance Criteria:**
- Given one valid text PDF inside the configured source directory, when the operator runs the command for a course, then persisted document and chunk rows include its checksum, requested course ID, stable ordering and IDs, one-based physical-page provenance, and 768-dimensional embeddings.
- Given a PDF with both usable and empty pages, when it is ingested, then usable pages are persisted and each empty page is diagnosed; a PDF with no usable text fails without partial writes.
- Given a path outside the configured directory, including a symlink that resolves outside it, when ingestion is requested, then the command rejects it before reading PDF content or writing to the database.
- Given the same source PDF ingested under two different course IDs, when both operations complete, then each document and all its chunks belong only to the requested course.
- Given an embedding or persistence failure, when ingestion ends, then the document and its chunks are rolled back together and the command exits nonzero without exposing credentials.

## Implementation Notes

- `PDF_SOURCE_DIR` defaults to `data/course-pdfs`; the CLI runs from the repository root and takes exactly one relative PDF path plus a course ID. Deterministic embeddings are the default; live Gemini use is opt-in.
- PDF bytes are checksummed before parsing. Extraction and embedding finish before a single SQLAlchemy transaction inserts the deterministic document and chunk IDs; failures leave no partial rows. Duplicate same-course source/checksum is rejected with a message pointing to Story 7.1's rerun behavior.
- A local PostgreSQL smoke check inserted a synthetic three-page PDF into an outer transaction, verified two course-scoped documents, four chunks, pages 1 and 3, and vector(768), then rolled back the outer transaction. No user PDF was ingested.

## Spec Change Log

## Review Triage Log

- **false** — The README command's module lookup was questioned; importing `app` from the repository root with the backend project environment resolves to `backend/app/__init__.py`, so the documented `uv --project backend` invocation has the required project path.
- **false** — A post-normalization chunk-length guard was suggested; `chunk_page_text` caps each slice at `start + chunk_size`, and the chunking tests assert the 1,000-character ceiling.
- **maybe-false (rejected as low)** — A parser exception outside the wrapped set was not demonstrated; establishing one requires a malformed PDF that makes pypdf raise an uncaught exception. The CLI still returns a generic nonzero failure, so any diagnostic difference is low impact.
- **low (rejected)** — An explicit whole-file/page limit would add a new policy for exceptional, very large operator-selected PDFs; no ordinary input or observed resource failure was shown.
- **medium; defer** — The persistence-failure rollback test uses a fake transaction context rather than a real failing PostgreSQL flush. The local PostgreSQL smoke check exercised the production session factory on success and rollback of the enclosing test transaction, but did not induce a partial-write failure; add that check when a reusable DB-backed integration-test fixture is established.
- **medium; patch applied** — Wrong-dimension and non-finite embedding outputs had explicit production rejection branches but no tests; parameterized tests now assert rejection occurs before a transaction opens.
- **low; patch applied** — The README described the ingest output but did not show database inspection; added concise queries for document metadata, chunk page/position, and vector dimensions.
- **medium; patch applied** — The operator CLI's settings/provider/session wiring was not covered; added tests for configured source root, deterministic default, output/warning behavior, nonzero errors, and engine disposal.
- **medium; patch applied** — Trimming each chunk removed boundary whitespace from the nominal overlap; chunks now preserve the exact 150-character source overlap, with a regression assertion on sentence/paragraph chunking.

## Design Notes

The user selected page-local chunks up to 1,000 characters with 150 characters of overlap. This is a starting point suited to the sample PDF's short pages, not a retrieval-quality claim; evaluate actual questions after retrieval is implemented. Derive stable document IDs from course ID, normalized source identity, and checksum; derive chunk IDs from the document ID and deterministic chunk position/content. This satisfies the stack's stable-ID contract while leaving repeated-ingestion policy to Story 7.1. Keep each chunk on one PDF page so a citation never points to multiple physical pages.

## Verification

**Commands:**
- `backend/.venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp C:\\BMAD\\_pytest_tmp_review_63 backend/tests` — passed: 31 backend tests, including CLI, chunk overlap, and ingestion matrix cases, without Gemini calls.
- `backend/.venv/Scripts/ruff.exe check backend/app backend/tests` — passed with no lint findings.
- `backend/.venv/Scripts/mypy.exe backend/app backend/tests` — passed with no type errors.
- `backend/.venv/Scripts/python.exe -m app.ingest --help` (from `backend/`) — passed and displayed the validated single-PDF inputs.

**Manual checks:**
- Apply the existing Alembic migrations, ingest a text-bearing fixture with deterministic embeddings, and inspect document/chunk rows for checksum, course isolation, page provenance, stable positions, and vector dimensions. Repeat with the same file under a second course ID; verify no rows cross course scope.
