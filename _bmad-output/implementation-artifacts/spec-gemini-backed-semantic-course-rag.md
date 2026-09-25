---
title: 'Gemini-backed semantic course RAG'
type: 'feature'
created: '2026-09-25'
status: 'done'
baseline_commit: 'ea2368db239e55460e9fa1468f86ee5df7f9bcc8'
route: 'full'
route_source: 'pinned'
review: 'thorough'
review_source: 'auto'
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-local-course-rag-assistant/stack.md'
  - '{project-root}/_bmad-output/specs/spec-local-course-rag-assistant/evaluation.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-6-context.md'
  - '{project-root}/_bmad-output/initiative-local-course-rag-assistant/epic-reliability-student-experience/tickets.toml'
---

<frozen-after-approval reason="human-owned intent ? do not modify unless human renegotiates">

## Intent

**Problem:** The local chat always uses deterministic test providers. Those embeddings hash whole inputs rather than representing meaning, so a natural question cannot retrieve a longer passage that answers it. The existing Cours1.pdf vectors were also created by the deterministic provider, so changing only query-time providers would mix incompatible vector spaces.

**Approach:** Add an explicit Gemini runtime mode that uses the same configured embedding provider for ingestion and query embeddings and Gemini for grounded answer generation. Persist embedding provenance, safely re-embed an unchanged source when its provider changes, and retain deterministic mode for offline development and tests.

## Boundaries & Constraints

**Always:** Keep Gemini API credentials server-side and fail clearly when Gemini mode lacks a key; never silently fall back between embedding spaces. Preserve course filtering, the 768-dimensional schema contract, physical-page provenance, and citation-ID validation. Perform provider-change re-embedding before an atomic database update so failure keeps existing vectors usable. Keep ordinary tests offline and deterministic.

**Never:** Remove or rewrite the existing PDF or database as a setup shortcut. Change model/dimension schema, add OCR, or implement collection-wide changed-file synchronization. Claim that citation-ID validation proves semantic entailment; full ambiguity and unsupported-answer policy remains Epic 7 Story 7.3.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Gemini run | Gemini mode, key present, existing same-checksum PDF | Re-embed stored chunks with Gemini, mark their provider, then allow same-course semantic query and cited answer | Commit vector/provider changes atomically |
| Missing key | Gemini selected, key absent | Startup and ingestion fail with a clear configuration message before provider requests | No credential values in logs or responses |
| Provider mismatch | Stored vectors use another or unknown provider | Startup refuses mixed-space retrieval; ingestion can safely re-embed the selected unchanged PDF | Failed embed/persist retains prior provider and vectors |
| Offline run | Deterministic mode, no external services | Existing deterministic tests and local workflow remain available | No live provider calls |

</frozen-after-approval>

## Code Map

- `backend/app/config.py` ? add explicit `rag_provider` selection with deterministic default and existing Gemini key/model settings.
- `backend/app/main.py` ? select matching embedding and generation adapters for answer requests; own and close Gemini clients through app lifecycle while preserving injection seams.
- `backend/app/ingest.py` ? use configured provider by default and preserve explicit provider selection for the one-file command.
- `backend/app/ingestion.py` ? recognize an existing same-checksum document; no-op when provider matches or precompute replacement vectors then atomically update chunks/provider metadata when it differs. Leave changed-source synchronization to Epic 7.2.
- `backend/app/models.py`, `backend/app/schema_guard.py`, `backend/alembic/versions/` ? record embedding provider per document, mark legacy rows unverified, and block service retrieval until all rows match runtime provider.
- `backend/tests/test_ingestion.py`, `backend/tests/test_persistence.py`, `backend/tests/test_answer_stream.py`, `backend/tests/test_providers.py` ? cover provider selection, mismatch rejection, successful re-embedding, rollback, and live adapter wiring with fakes only.
- `compose.yaml`, `.env.example`, `README.md` ? document server-side key setup and the safe steps to re-embed the existing `data/course-pdfs/Cours1.pdf` without deleting database or source files.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/config.py`, `backend/app/main.py`, `backend/app/ingest.py` ? add explicit deterministic/Gemini mode selection for both query and ingest providers so a configured key enables semantic RAG without silently changing offline behavior.
- [x] `backend/app/models.py`, `backend/alembic/versions/`, `backend/app/schema_guard.py` ? persist provider provenance and prevent mixed or unknown vectors from being queried as one embedding space.
- [x] `backend/app/ingestion.py` ? re-embed same-checksum documents when provider changes and atomically replace only embeddings/provider metadata; preserve existing data if provider or transaction fails.
- [x] Backend tests ? verify Gemini is selected with injected/fake clients, key errors are safe, deterministic behavior remains offline, and migration/re-embedding/schema guard handle legacy vectors.
- [x] `compose.yaml`, `.env.example`, `README.md` ? explain how to set Gemini mode and key, migrate schema, re-embed Cours1.pdf, then use natural-language questions in chat.

**Acceptance Criteria:**
- Given Gemini mode and a valid server key, when the existing PDF is ingested or re-embedded and a paraphrased course question is submitted, then question and chunk vectors use the same Gemini model and the API returns answer text with resolvable same-course citations.
- Given vectors created by a different or unknown provider, when the API starts in Gemini mode, then it refuses retrieval with a clear re-embedding instruction rather than returning misleading results.
- Given an embedding or database failure during provider-change re-embedding, when ingestion ends, then the previously committed vectors and their provider marker remain intact.
- Given no API key and deterministic mode, when offline tests run, then provider calls remain deterministic and require no external service.
- Given Gemini mode without a key, when the API starts or ingestion runs, then it fails clearly without exposing secrets or silently using deterministic vectors.

## Implementation Notes

## Spec Change Log

## Review Triage Log

- blind-hunter — medium, patch: provider-change re-embedding now checks each stored chunk ID and position before updating its vector; tests cover a same-count identity mismatch and preserve existing rows.
- edge-case-hunter — medium, patch: this independently reported the same chunk identity mismatch; the same validation and refusal test address it.
- blind-hunter — low, rejected: a same-provider document with missing chunks would return a zero or reduced chunk count, but reaching this state requires pre-existing database corruption or manual deletion, and a protective guard would add a branch for that uncommon state.
- blind-hunter — medium, patch: legacy NULL provider rows now have tests for successful re-embedding and for provider failure preserving NULL provenance and existing vectors.
- blind-hunter — medium, patch: count-mismatch refusal now has coverage asserting provider and existing rows remain unchanged.
- blind-hunter — medium, patch: a fake-backed Gemini-mode stream test now runs the actual answer flow through retrieval and checks the returned same-course citation.
- intent-alignment — medium, patch: the stated chat-level outcome previously lacked evidence beyond backend adapter wiring; the new stream integration test covers retrieval and citation serialization using fakes.
- blind-hunter — medium, patch: README now instructs operators to query every NULL or mismatched document and re-embed each unchanged source before starting the API.
- blind-hunter — medium, patch: Gemini-owned clients now close when schema validation fails during startup; a lifecycle test covers this failure path.
- verification-gap — medium, patch: CLI tests now verify configured Gemini selection and an explicit deterministic override.

## Design Notes

Gemini is opt-in so starting the stack or running tests does not incur network calls or API charges. Switching vector spaces is an explicit re-embedding operation; the API must never compare vectors generated by different embedding implementations.

## Verification

**Commands:**
- `uv run --project backend --extra dev pytest backend/tests` ? expected: all offline backend tests pass with no live Gemini calls.
- `uv run --project backend --extra dev ruff check backend` ? expected: lint succeeds.
- `uv run --project backend --extra dev mypy backend/app backend/tests` ? expected: type checks succeed.
- `cd frontend && npm test -- --run && npm run lint && npm run typecheck && npm run build` ? expected: frontend checks pass.
- `docker compose config` ? expected: service environment includes provider mode and remains valid.
