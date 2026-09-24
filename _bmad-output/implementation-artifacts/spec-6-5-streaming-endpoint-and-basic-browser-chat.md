---
title: 'Streaming endpoint and basic browser chat'
type: 'feature'
created: '2026-09-24'
status: 'done'
baseline_commit: '5c0de0d45ee417d0f291cea5ce0f026a45585769'
route: 'full'
route_source: 'auto'
review: 'thorough'
review_source: 'auto'
lenses_ran: ['blind-hunter', 'edge-case-hunter', 'verification-gap', 'intent-alignment']
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-6-context.md'
  - '{project-root}/_bmad-output/initiative-local-course-rag-assistant/epic-working-rag-tracer/tickets.toml'
  - '{project-root}/_bmad-output/initiative-local-course-rag-assistant/epic-working-rag-tracer/epic-working-rag-tracer.md'
  - '{project-root}/_bmad-output/initiative-local-course-rag-assistant/epic-working-rag-tracer/story-course-filtered-grounded-cited-answer.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The supported course answer path exists as backend orchestration, but it is not exposed through an API or browser interaction. Developers cannot demonstrate a grounded cited answer arriving in the local chat UI.

**Approach:** Expose the existing validated answer path through a versioned typed stream for deltas, citation metadata, completion, clarification, abstention, and errors. Add a basic Next.js chat that submits a course question, displays incremental answer text and citation metadata, and prevents duplicate submissions.

## Boundaries & Constraints

**Always:** Preserve the answer path's course filtering and citation validation. Keep provider calls injectable and deterministic by default for offline development and tests. Send only typed, versioned events; on the supported success path, deliver answer deltas before citations and exactly one terminal completion, with no event after termination. Define clarification and abstention events without implementing their decision behavior. Keep credentials server-side.

**Never:** Change ingestion, persistence, retrieval, or answer-generation semantics; expose credentials in the browser; add live provider requirements to ordinary tests; implement Epic 7 clarification/abstention policy or full student failure UX.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Supported answer | Valid course ID and non-empty question with retrieved evidence | Versioned delta events, citation metadata, then one completion event | No error expected |
| Invalid request or answer failure | Empty/invalid question, no evidence, provider or validation failure | One terminal error event and no completion event | Safe message; do not expose credentials or internals |
| Browser stream | User submits while idle | Text appears incrementally; filename, physical page, and excerpt metadata are visible | Disable duplicate submission; show stream failure and allow retry |

</frozen-after-approval>

## Code Map

- `backend/app/main.py` — `create_app`, existing readiness route, startup schema guard, and CORS; add `POST /api/v1/answers/stream` without changing readiness behavior.
- `backend/app/answering.py` — `answer_question` returns validated `GroundedAnswer` claims and same-course citations; reuse unchanged and only stream its validated result.
- `backend/app/db.py` — `create_database_engine` and `create_session_factory`; use the existing database/session boundary.
- `backend/app/providers/protocols.py`, `backend/app/providers/deterministic.py`, `backend/app/providers/gemini.py` — injectable provider contracts/adapters and offline doubles; keep ordinary execution/tests deterministic.
- `backend/tests/test_readiness.py` — existing app/readiness coverage; add focused stream endpoint coverage with fake sessions/providers, no database or Gemini service.
- `frontend/lib/api.ts` and `frontend/lib/api.test.ts` — API base URL and readiness convention; add a typed stream request/parser with chunk-boundary and terminal-event validation.
- `frontend/app/page.tsx`, `frontend/app/globals.css`, `frontend/components/readiness-panel.tsx` — current scaffold UI and styling; preserve readiness while adding a small chat interaction.
- `frontend/package.json` — existing test, lint, typecheck, and build commands.
- `README.md`, `compose.yaml` — local stack and documented developer flow; explain the chat path and its course ID input.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/main.py` and a focused stream-contract module — add typed version-1 NDJSON events (`delta`, `citations`, `completed`, `clarification`, `abstention`, `error`) and `POST /api/v1/answers/stream` wired to `answer_question`; allow POST in CORS and preserve readiness/startup behavior.
- [x] `backend/tests/test_readiness.py` or a dedicated stream test module — cover event order, citation provenance, exactly one completion, terminal errors, and no events after termination using injected offline dependencies.
- [x] `frontend/lib/api.ts` — submit course ID/question and parse versioned stream events across arbitrary network chunk boundaries; reject malformed, unknown, duplicate-terminal, or post-terminal events.
- [x] `frontend/app/page.tsx` and `frontend/app/globals.css` — add accessible course ID/question controls, incremental answer display, citation filename/page/excerpt, progress, and duplicate-submit prevention while retaining readiness.
- [x] `frontend/lib/api.test.ts` and a chat component test — cover split event frames, ordering, errors, visible citations, and duplicate submissions without live API calls.
- [x] `README.md` — document starting the stack, using the course chat, and the versioned stream contract at a developer level.

**Acceptance Criteria:**
- Given a valid course question with supported retrieved evidence, when the browser submits it, then the API emits typed version-1 answer deltas before citation metadata and exactly one terminal completion, with no duplicated text or later events.
- Given a completed stream, when it is displayed in the browser, then the answer appears incrementally and each visible citation includes its filename, physical page, and supporting excerpt.
- Given an invalid request or answer-path failure, when the stream is consumed, then it terminates with one safe error event and never reports successful completion.
- Given a user is already waiting for a response, when they attempt another submission, then the UI prevents a duplicate request and communicates progress.
- Given deterministic providers and no external services, when the endpoint and UI checks run, then they verify the stream contract without live Gemini or database access.

## Implementation Notes

Use newline-delimited JSON (`application/x-ndjson`) with an envelope containing `version: 1` and a discriminating `type`. Emit text only after `answer_question` has validated the generated claims and citation IDs; do not expose raw model JSON or unvalidated text. Clarification and abstention are event types for the later policy story, not outcomes to invent here.

## Spec Change Log

## Review Triage Log

- **false ? blind-hunter #1 (temporary fixture files):** `.pytest-tmp/` was already present as untracked user data before Story 6.5 implementation began; no implementation step created or edited it. It is preserved as requested.
- **false ? blind-hunter #2 (mojibake):** UTF-8 source inspection confirms the intended U+2026 ellipsis, U+2019 apostrophe, and U+00B7 middle dot. The displayed `???`/`???`/`??` sequences came from the review/terminal rendering path; no source correction was needed.
- **patch ? blind-hunter #3 (claims concatenate):** Insert a separating space delta between independently validated claims. `test_stream_separates_multiple_claims` verifies the rendered stream text keeps the claims distinct.
- **false ? blind-hunter #4 (clarification/abstention event order):** Clarification and abstention are contract event types only; this story explicitly does not define their policy. The parser treats them as terminal events and rejects all subsequent events. It permits them before or after citations because no ordering rule for these future outcomes is specified.
- **patch ? blind-hunter #5 (reader cleanup):** `consumeAnswerStream` now cancels the reader if parsing/callback processing throws and releases the lock in `finally`.
- **defer ? blind-hunter #6 (engine lifetime):** The endpoint currently creates/disposes its own engine when no session factory is injected. Revisit application-scoped engine lifecycle if this API becomes a sustained service; it is not needed to demonstrate the local story path. Added to deferred work.
- **patch ? blind-hunter #7 (request fields):** `StreamRequest` now exposes nullable strict string fields rather than `object`; blank/missing values produce one safe terminal error event. Added endpoint coverage.
- **defer ? blind-hunter #8 (semantic claim support):** This concerns existing answer-generation validation in `backend/app/answering.py`, outside this story's frozen requirement to reuse that path unchanged. Added to deferred work.
- **defer ? blind-hunter #9 (retrieval database exception mapping):** This concerns existing answer-path error handling in `backend/app/answering.py`, outside this story's streaming/UI scope. Added to deferred work.
- **defer ? intent-alignment (full browser-to-API system exercise):** The UI is wired to `requestAnswerStream`; backend endpoint tests and frontend API-client tests cover each side of the wire contract without live services. A full compose/browser smoke test remains useful but is outside the deterministic offline acceptance checks. Added to deferred work.
- **lens access failure ? edge-case-hunter:** Review agent could not read its instruction file due filesystem access denial and stopped without a review.
- **lens access failure ? verification-gap:** Review agent could not read its instruction file due filesystem access denial and stopped without a review.

## Design Notes

Keep the NDJSON parser independent of the UI so it can handle arbitrary byte boundaries and terminal-state violations deterministically. Only the successful supported path emits a completion event; a failed path emits an error terminal event. The citation event follows deltas so the UI can render answer text as it arrives and then attach verified provenance.

## Verification

**Commands:**
- `uv run --project backend --extra dev pytest backend/tests` — expected: backend stream and existing offline tests pass.
- `cd frontend && npm test -- --run` — expected: stream parser and chat component tests pass.
- `cd frontend && npm run lint && npm run typecheck && npm run build` — expected: frontend checks succeed.
- `docker compose config` — expected: local service configuration remains valid.
