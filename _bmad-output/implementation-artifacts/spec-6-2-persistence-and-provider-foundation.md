---
title: 'Persistence and provider foundation'
type: 'feature'
created: '2026-09-24'
status: 'done'
baseline_commit: '3578e8b5a3d517c9303dbda611c08c54e1513442'
route: 'full'
route_source: 'auto'
review: 'thorough'
review_source: 'auto'
lenses_ran: ['blind-hunter', 'intent-alignment']
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-6-context.md'
  - '{project-root}/_bmad-output/specs/spec-local-course-rag-assistant/SPEC.md'
  - '{project-root}/_bmad-output/specs/spec-local-course-rag-assistant/stack.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 6.1 established the local application scaffold, but the backend has no persistent course/document/chunk schema, schema migration, semantic embedding configuration guard, or model-provider boundary. Later ingestion and answer stories need these foundations to preserve course scope and evidence provenance.

**Approach:** Add the PostgreSQL/pgvector schema and Alembic migration, validate runtime model configuration against the migrated embedding metadata at startup, and provide thin direct Gemini embedding and generation adapters alongside deterministic offline doubles.

## Boundaries & Constraints

**Always:** Use SQLAlchemy, Alembic, PostgreSQL with pgvector, Pydantic, and the existing direct Google GenAI SDK dependency. Store course identity, document checksum and source filename, page count, chunk position and page provenance, extracted text, and a 768-dimensional vector. Enforce document/chunk course consistency, valid page/position values, uniqueness needed for stable records, and course/vector query indexes. Couple `gemini-embedding-2` to 768 dimensions in persisted schema metadata and reject a mismatch at application startup. Keep Gemini credentials server-side and optional for offline startup; provider calls must be injectable and tests must use deterministic doubles without live network access. Preserve story 6.1 readiness behavior and avoid adding LangChain or LlamaIndex.

**Never:** Implement PDF ingestion, collection synchronization or changed-file replacement, retrieval orchestration, chat/generation orchestration, streaming, citations, or evidence sufficiency behavior. Do not call Gemini during import, startup, or ordinary tests. Do not silently run with an incompatible vector model or dimensionality.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh local database | Empty pgvector database and valid defaults | Migration creates the required schema and seeds its embedding metadata; local API starts after migration | Migration failure prevents API startup with a useful diagnostic |
| Incompatible embedding settings | Unsupported embedding model or dimensions that differ from schema metadata | Startup is rejected before readiness is reported | Raise a clear configuration/schema compatibility error without exposing credentials |
| Offline provider use | No Gemini key and deterministic test doubles injected | Embedding/generation contracts return controlled results without network access | Live Gemini adapter reports missing credentials only when invoked |

</frozen-after-approval>

## Code Map

- `backend/app/config.py` — existing structural `Settings`; extend with semantic validation while keeping secrets masked and optional for offline operation.
- `backend/app/main.py` — existing FastAPI factory and readiness route; add startup schema compatibility validation without changing the readiness response contract.
- `backend/pyproject.toml` and `backend/uv.lock` — already contain Alembic, SQLAlchemy, pgvector, psycopg, and `google-genai`; avoid dependency changes unless investigation proves one is required.
- `compose.yaml` and `backend/Dockerfile` — local pgvector service and backend command; preserve the one-command local stack and ensure migrations precede API startup if required.
- `backend/tests/test_config.py` and `backend/tests/test_readiness.py` — existing offline settings/readiness coverage; extend with invalid semantic config and startup guard behavior.
- `backend/app/` and `backend/tests/` — no database models/session, Alembic environment/revisions, provider adapters/doubles, or migration tests exist yet; add focused modules and tests for this story.
- `_bmad-output/implementation-artifacts/epic-6-context.md` — compiled epic constraints and ownership boundaries.
- `_bmad-output/implementation-artifacts/spec-application-and-test-scaffold.md` — completed 6.1 continuity record; preserve its readiness and local-stack behavior.

## Tasks & Acceptance

**Execution:**
- [ ] `backend/app/models.py` and database session module — define course-scoped document, chunk, and embedding-schema metadata mappings with composite course integrity, provenance, checks, uniqueness, and query indexes.
- [ ] `backend/alembic.ini`, `backend/alembic/env.py`, and `backend/alembic/versions/` — configure migrations and create the initial schema, including pgvector and the fixed embedding model/dimension metadata.
- [ ] `backend/app/config.py` and startup wiring — reject unsupported model/dimension settings and compare runtime embedding configuration with persisted schema metadata before the API becomes ready.
- [ ] `backend/app/providers/` — define narrow embedding and generation contracts, direct Gemini implementations, and deterministic doubles; keep construction injectable and avoid provider calls during startup.
- [ ] `backend/tests/` — verify migration columns, constraints and indexes, metadata compatibility and mismatch errors, offline provider outputs, and secret-safe behavior.
- [ ] `compose.yaml` and `README.md` — preserve the documented one-command local startup by applying migrations before the API server starts, and document any migration operation needed by developers.

**Acceptance Criteria:**
- Given an empty supported PostgreSQL/pgvector database, when the documented migration is applied, then documents and chunks expose all required course, checksum, filename, page-count, position, provenance, extracted-text, and `vector(768)` fields with the required constraints and indexes.
- Given the migrated schema metadata for `gemini-embedding-2` at 768 dimensions, when the API starts with matching settings, then startup succeeds and the existing readiness response remains unchanged.
- Given an unsupported embedding model, a dimension mismatch, or persisted schema metadata that differs from runtime settings, when the API starts, then startup fails clearly before reporting ready.
- Given tests with deterministic provider doubles and no Gemini credentials or network, when provider contract tests run, then embedding and generation results are repeatable and no live call is attempted.
- Given no Gemini key, when the local stack starts without invoking live providers, then startup succeeds and neither environment secrets nor provider credentials are exposed in readiness output or browser code.

## Implementation Notes

## Design Notes

Keep the provider seam independent of orchestration: this story establishes typed direct adapters and controllable doubles, while stories 6.3 and 6.4 decide when to call them. Treat persisted embedding model and dimensions as one compatibility record; the migration establishes the expected pair, and startup compares it with settings before serving requests. In the local Compose stack, migration must complete before the API process starts so a fresh developer checkout still follows one documented startup command.

## Review Triage Log

| Finding | Verdict | Disposition |
|---|---|---|
| Chunk page numbers can exceed the source document page count. | Valid | Deferred to ingestion, where page extraction and document page-count context are available together; the persistence schema already enforces positive page values. |
| SHA-256 checksum constraint allowed non-hex characters. | Valid | Fixed with a PostgreSQL hex-format check in the foundation migration. |
| Whitespace-only chunk text passed the nonempty check. | Valid | Fixed using `btrim` in the foundation migration. |
| Global HNSW index can under-return after course filtering. | Valid | Deferred to retrieval implementation; this story creates query indexes only and does not implement retrieval/ranking. Retrieval must scope course before ranking or choose a course-aware strategy. |
| Deterministic embedding repeated the same 32-byte digest. | Valid | Fixed by hashing a block counter with the input to populate dimensions. This remains a deterministic offline test double, not a semantic embedding model. |
| Gemini generation accepted blank response text. | Valid | Fixed; blank and missing text now fail explicitly. |
| Gemini adapters did not expose client cleanup. | Valid | Fixed with `close()` methods that release and clear the SDK client. |
| Migration 0002 attempted to drop a constraint name not created by 0001, and `btrim` did not cover tabs/newlines. | Valid | Fixed by retaining 0001’s original constraint names and adding revision 0003 with a POSIX whitespace check; verified by applying revisions 0002 and 0003 to local PostgreSQL. |
| Automated migration tests did not exercise PostgreSQL constraints/indexes. | Valid | Deferred as automated coverage because this repository has no isolated DB-test fixture; the migration was applied to the local PostgreSQL/pgvector service and startup/readiness were verified, but CI-level enforcement tests remain desirable. |
| Edge-case and verification-gap reviewer prompts were inaccessible in the rendered workflow snapshot. | Process limitation | Both reviewers stopped without reviewing the diff; blind-hunter and intent-alignment findings were triaged above. |

## Review Summary

The blind-hunter and intent-alignment reviews completed. The verification-gap and edge-case-hunter prompts could not be read due to the rendered snapshot being inaccessible to those reviewers; their review passes did not run. The local database successfully applied migration `20260924_0002`, and the API returned ready afterward.

## Verification

**Commands:**
- `docker compose config` — expected: local service configuration resolves.
- `uv run --project backend --extra dev pytest backend/tests` — expected: migration, config, provider, and existing readiness tests pass offline.
- `uv run --project backend --extra dev ruff check backend` — expected: backend lint passes.
- `uv run --project backend --extra dev mypy backend/app backend/tests` — expected: backend typecheck passes.
- `docker compose up --build` — expected: on a fresh database, migration completes before the API becomes healthy and the existing readiness route reports ready.
