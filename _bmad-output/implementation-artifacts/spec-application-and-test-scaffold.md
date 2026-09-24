---
title: 'Application and test scaffold'
type: 'feature'
created: '2026-09-24'
status: 'done'
baseline_commit: 'd6d0e4b42af240e10508fb4b5c66dc576c3803a9'
route: 'full'
route_source: 'auto'
review: 'thorough'
review_source: 'auto'
lenses_ran: ['blind-hunter', 'edge-case-hunter', 'verification-gap', 'intent-alignment']
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/specs/spec-local-course-rag-assistant/stack.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The repository has product contracts but no runnable application, database service, or automated quality baseline. Developers need a clean foundation that proves the browser can observe API readiness before later RAG stories add domain behavior.

**Approach:** Scaffold a typed FastAPI backend, a TypeScript Next.js frontend, and PostgreSQL with pgvector under Docker Compose. Provide one documented startup command, structural configuration shapes, an offline UI-to-API readiness path, and deterministic test, lint, and typecheck commands.

## Boundaries & Constraints

**Always:** Use FastAPI, Next.js, PostgreSQL/pgvector, SQLAlchemy, Alembic, Pydantic, `pypdf`, and the direct Gemini SDK boundaries required by the implementation contract. Keep secrets server-side and uncommitted; expose only non-sensitive readiness state. The default readiness and automated checks must run without Gemini credentials or network calls. Use a single Compose definition and a single documented command to start database and both applications from a prepared clean checkout.

**Never:** Add LangChain or LlamaIndex. Do not implement database models or migrations, ingestion, provider adapters, retrieval, chat/streaming, citations, semantic runtime configuration validation, or the embedding-model/schema compatibility guard. Do not add authentication, production deployment, runtime PDF management, or later student-experience behavior.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Stack ready | Compose services start and the API is reachable | Backend readiness returns a typed healthy response; browser displays API ready | No error expected |
| API unavailable | Browser loads while the API cannot be reached | Browser displays an explicit unavailable state without crashing or inventing readiness | Network failure is caught and rendered as unavailable |
| Missing optional provider secret | No Gemini API key is configured | Scaffold and readiness remain runnable offline | Provider-dependent behavior is not invoked |

</frozen-after-approval>

## Code Map

- `.gitignore` — existing language, build, secret, and local-data exclusions; preserve and extend only if generated scaffold artifacts require it.
- `.env.example` — document non-secret backend, browser API URL, database, and future provider configuration shapes.
- `compose.yaml` — define pgvector PostgreSQL, FastAPI, and Next.js services with health-aware startup and local development ports.
- `README.md` — prerequisite, clean-checkout setup, one-command startup, URLs, shutdown, and verification commands.
- `backend/pyproject.toml` — Python package, fixed stack dependencies, and pytest/Ruff/mypy configuration.
- `backend/Dockerfile` — reproducible FastAPI development service image.
- `backend/app/config.py` — Pydantic structural settings and safe defaults; no semantic model/schema guard yet.
- `backend/app/main.py` — application factory and versioned readiness endpoint.
- `backend/tests/` — configuration-default and readiness API tests.
- `frontend/package.json` and configuration files — Next.js/TypeScript scripts plus lint, typecheck, and Vitest setup.
- `frontend/app/` — minimal server-rendered shell and client readiness display.
- `frontend/lib/api.ts` — typed readiness request with unavailable-state handling.
- `frontend/lib/api.test.ts` and `frontend/components/readiness-panel.test.tsx` — direct HTTP-helper and UI readiness behavior tests.

## Tasks & Acceptance

**Execution:**
- [x] `.env.example`, `compose.yaml`, backend/frontend Dockerfiles — define a non-secret, health-aware local stack started by `docker compose up --build`.
- [x] `backend/pyproject.toml`, `backend/app/config.py`, `backend/app/main.py` — create the typed FastAPI package, structural settings, and `/api/v1/readiness` contract.
- [x] `backend/tests/test_config.py`, `backend/tests/test_readiness.py` — verify offline defaults, secret-safe serialization, and readiness response behavior.
- [x] `frontend/package.json` plus Next.js, TypeScript, ESLint, and Vitest configuration — establish reproducible frontend build and quality commands.
- [x] `frontend/app/page.tsx`, readiness UI, and `frontend/lib/api.ts` — fetch and visibly render ready/unavailable API state without exposing server secrets.
- [x] `frontend/lib/api.test.ts`, `frontend/components/readiness-panel.test.tsx` — cover successful, malformed, and failed readiness behavior without a live backend.
- [x] `README.md` — document prerequisites, environment preparation, the single startup command, service URLs, shutdown, and all automated checks.

**Acceptance Criteria:**
- Given a clean prepared checkout with Docker available, when `docker compose up --build` is run, then PostgreSQL/pgvector, FastAPI, and Next.js start and the browser reports API readiness.
- Given no Gemini API key and no live Gemini access, when the local stack and default automated checks run, then readiness and tests succeed without provider calls.
- Given backend and frontend source, when documented test, lint, and typecheck commands run, then all initial checks pass.
- Given the committed configuration examples and browser bundle, when inspected, then no real secret is committed or returned to the browser.

## Implementation Notes

- Added a Docker Compose local stack with PostgreSQL/pgvector, a versioned FastAPI readiness response, and a Next.js readiness panel. Configuration holds database parts, secret provider credentials, model defaults, retrieval/evidence limits, and the local frontend origin.
- Constructed database URLs with SQLAlchemy `URL.create` so reserved password characters remain valid. Python and JavaScript lockfiles drive the container installs; `.env`, generated caches, and build metadata stay ignored.
- Review fixes added local CORS, API response-shape validation, direct readiness-helper tests, complete Compose environment mappings, `npm ci`, and lockfile-based backend installation.
- Compose started all three services successfully. PostgreSQL and API reported healthy; the readiness endpoint returned the typed `ready` response with the configured browser origin, and Next.js served the page with HTTP 200.
- Full verification passed: Compose config, 5 backend tests, Ruff, mypy, 6 frontend tests, ESLint, TypeScript, and Next.js production build. Pytest emitted a Starlette/httpx deprecation warning. npm reported 7 dependency advisories during install; no dependency remediation was applied in this scaffold pass.

## Spec Change Log

## Review Triage Log

| Finding | Verdict / route | Evidence and disposition |
|---|---|---|
| `.env` is not ignored | false / reject | The existing root `.gitignore` already ignores `.env` and `.env.*`, while explicitly allowing `.env.example`. |
| Browser readiness can be blocked by missing CORS | medium / patch | The API now allows the configured local frontend origin; backend test and Compose-origin request both returned the matching CORS header. |
| Compose omits documented structural settings | medium / patch | Compose now passes model, embedding dimensions, retrieval/evidence limits, and frontend origin into the API container. |
| Next public API URL is unavailable at runtime | false / reject | The local service runs `next dev`; Compose supplies the environment before that server starts. The tested URL defaults to the same localhost API address. |
| URL-safe credentials are assumed | medium / patch | A `pa@ss:/?#%` password previously parsed into the wrong host; SQLAlchemy `URL.create` now encodes it, and a round-trip test passes. |
| Any successful JSON response is accepted as readiness | medium / patch | The client now checks both contract fields and rejects malformed 200 responses; a focused test covers the invalid shape. |
| Frontend Docker install does not enforce its lockfile | low / patch | Replaced `npm install` with `npm ci`; the Docker image rebuilt successfully using the checked-in lockfile. |
| Backend Docker install ignores the Python lock | medium / patch | The image now runs `uv sync --locked --no-dev`; Compose rebuilt successfully from `backend/uv.lock`. |
| Readiness panel tests mock the API helper | medium / patch | Added controlled-fetch tests for URL/cache settings, success, malformed JSON shape, and non-OK responses; all pass. |
| Secret serialization is not covered by current API paths | false / reject | The only route returns a fixed `ReadinessResponse`; settings and credentials are not serialized by any current endpoint, and `SecretStr` remains masked. |
| A malformed 200 payload could display API ready | medium / patch | Same runtime guard now rejects payloads unless both `status` and `service` match; component tests render the unavailable state after rejection. |
| Browser-to-API URL and failure path lack direct tests | medium / patch | The helper tests assert the requested URL and non-OK handling; Compose checks confirmed the service endpoint and page are reachable. |
| Intent expects browser-observable API readiness but tests are isolated | medium / patch | Added CORS and direct helper contract coverage, then ran the three-service stack and confirmed the local origin receives the readiness response. |

## Design Notes

Compose is the orchestration boundary because it makes the acceptance command identical across supported Docker environments and can express database health before API startup. Readiness is deliberately application-level and offline: it proves UI-to-API wiring but does not claim that later database schema or Gemini compatibility checks have passed.

## Verification

**Commands:**
- `docker compose config` — expected: the three-service definition resolves with no invalid interpolation.
- `uv run --project backend --extra dev pytest backend/tests` — expected: backend configuration and readiness tests pass.
- `uv run --project backend --extra dev ruff check backend` — expected: backend lint passes.
- `uv run --project backend --extra dev mypy backend/app backend/tests` — expected: backend typecheck passes.
- `npm --prefix frontend test -- --run` — expected: frontend readiness tests pass.
- `npm --prefix frontend run lint` — expected: frontend lint passes.
- `npm --prefix frontend run typecheck` — expected: TypeScript typecheck passes.
- `npm --prefix frontend run build` — expected: production compilation succeeds.
