# Local Course RAG Assistant

This repository contains the local application scaffold for the course assistant: a FastAPI service, a Next.js interface, and PostgreSQL with pgvector.

## Prerequisites

- Docker Desktop with Docker Compose v2
- Node.js 22 or newer and npm
- Python 3.12 or newer and `uv`

## Start the local stack

The defaults work without a `.env` file. Copy `.env.example` to `.env` only when you want to override them. From the repository root run:

```sh
docker compose up --build
```

Open the web app at <http://localhost:3000>. The readiness panel reports whether the API at <http://localhost:8000/api/v1/readiness> is reachable. PostgreSQL listens on port 5432. The checked-in defaults are for local development only; replace them before using this stack outside your machine. The API container applies Alembic migrations before starting, and its startup check verifies that the database vector schema matches the configured Gemini embedding model and dimensions. Gemini is not called at startup or by readiness, and no API key is needed until a live provider is invoked.

Stop the services with `Ctrl+C`, or run `docker compose down`. Run `docker compose down -v` only when you intend to remove the local database volume.

## Automated checks

Backend, from the repository root:

```sh
uv run --project backend --extra dev pytest backend/tests
uv run --project backend --extra dev ruff check backend
uv run --project backend --extra dev mypy backend/app backend/tests
```

Frontend:

```sh
cd frontend
npm install
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Validate the Compose definition with `docker compose config`.
