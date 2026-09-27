# Local Course RAG Assistant

This repository contains the local application scaffold for the course assistant: a FastAPI service, a Next.js interface, and PostgreSQL with pgvector.

## Prerequisites

- Docker Desktop with Docker Compose v2
- Node.js 22 or newer and npm
- Python 3.12 or newer and `uv`

## Start the local stack

The defaults work offline without a `.env` file. Copy `.env.example` to `.env` to opt into Gemini by setting `RAG_PROVIDER=gemini` and `GEMINI_API_KEY` (the key stays in the server environment). From the repository root run:

```sh
docker compose up --build
```

To use OpenAI for answer generation while keeping the current embedding provider, set `ANSWER_PROVIDER=openai` and `OPENAI_API_KEY` in `.env`. `OPENAI_ANSWER_MODEL` selects the OpenAI model (default `gpt-4.1-mini`). `RAG_PROVIDER` continues to select embeddings; leaving it as `deterministic` avoids provider changes to existing vectors. The OpenAI key is only passed to the API container.

To use Groq instead, set `ANSWER_PROVIDER=groq` and `GROQ_API_KEY` in `.env`. `GROQ_ANSWER_MODEL` selects the Groq model (default `openai/gpt-oss-20b`). Groq uses its OpenAI-compatible chat completions API; `RAG_PROVIDER` continues to control embeddings. Check Groq's current model and free-tier limits in its console documentation before choosing a model.

Open the web app at <http://localhost:3000>. The readiness panel reports whether the API at <http://localhost:8000/api/v1/readiness> is reachable. PostgreSQL listens on port 5432. The checked-in defaults are for local development only; replace them before using this stack outside your machine. The API container applies Alembic migrations before starting, and its startup check verifies that the database vector schema matches the configured Gemini embedding model and dimensions. Gemini is not called at startup or by readiness, and no API key is needed until a live provider is invoked.

## Ask a course question

Add a unique course name and searchable PDF in the web app at <http://localhost:3000>. Each upload creates one course; the API validates and ingests the PDF, then selects the course so you can ask a question right away. PDFs must be text-searchable and no larger than 25 MB. Existing courses appear in the selector with their PDF filenames. Uploaded PDFs are stored beneath `PDF_SOURCE_DIR`; use the local ingestion command below when you need CLI-based ingestion. The default local API uses deterministic embedding and generation providers, so local development does not require Gemini credentials.

Chat messages remain in browser memory for the current page session. Follow-up questions include recent turns for context, while each answer retrieves evidence only from the selected course. Starting a new chat or reloading the page clears the conversation.

The API accepts `POST /api/v1/answers/stream` with JSON `{ "course_id": "course-1", "question": "..." }` and returns newline-delimited JSON (`application/x-ndjson`). Each event has `version: 1` and a `type`: supported answers emit one or more `delta` events, then `citations` with filename, one-based physical page, and excerpt, then exactly one terminal `completed` event. Failures emit one safe terminal `error` event. `clarification` and `abstention` are reserved contract event types for later policy work.

The optional `history` field contains up to 12 recent `{ "role": "user" | "assistant", "content": "..." }` turns to interpret follow-up questions. History is held in browser memory and is not persisted.

## Ingest one PDF

Place a PDF beneath `data/course-pdfs/` (or set `PDF_SOURCE_DIR` in `.env` to another local directory). Start the database and API so the schema is migrated, then run the one-file command from the repository root:

```sh
mkdir -p data/course-pdfs
cp /path/to/Cours1.pdf data/course-pdfs/
docker compose up --build -d database api
uv run --project backend python -m app.ingest --course-id course-1 --pdf Cours1.pdf
```

Ingestion uses `RAG_PROVIDER` by default; `--provider deterministic` or `--provider gemini` can explicitly override it for the one selected PDF. Gemini requires `GEMINI_API_KEY`. Documents record which embedding provider created their vectors. API startup refuses retrieval if any document is unverified or belongs to another provider. Re-ingesting the same unchanged PDF with the configured provider safely replaces only its embeddings and provider marker in one transaction; failed embedding or persistence leaves the old vectors intact. A matching provider is a no-op.

The startup guard checks every document, so switching providers requires re-embedding every row whose marker differs from the selected provider (including legacy rows marked `NULL`). Before starting the API, configure `RAG_PROVIDER=gemini` and `GEMINI_API_KEY` in `.env`, start only PostgreSQL, apply migrations, and list all affected documents:

```sh
docker compose up -d database
uv run --project backend python -m alembic -c backend/alembic.ini upgrade head
docker compose exec database psql -U course_rag -d course_rag -c \
  "SELECT course_id, source_filename, embedding_provider FROM documents WHERE embedding_provider IS DISTINCT FROM 'gemini' ORDER BY course_id, source_filename;"
```

For **each returned row**, make sure that exact unchanged PDF is present beneath `PDF_SOURCE_DIR`, then run the one-file command using its course ID and source filename (for example, for `course-1` and `Cours1.pdf`):

```sh
uv run --project backend python -m app.ingest --course-id course-1 --pdf Cours1.pdf
```

Repeat until the query returns no rows, then start the API and web app:

```sh
docker compose up --build -d api web
```

Do not delete or replace source files or database rows as a shortcut. If an affected PDF is missing or its bytes have changed, restore the original unchanged source before re-embedding; changed-source synchronization is outside this workflow. If the backend command is run outside the container, make sure `DATABASE_HOST=localhost` is set. After startup, use the chat with a natural-language question about the re-embedded course. Deterministic mode remains the default and does not make external calls.

To inspect the saved page provenance and course ownership, query the local database:

```sh
docker compose exec database psql -U course_rag -d course_rag -c \
  "SELECT id, course_id, source_filename, checksum, page_count FROM documents ORDER BY created_at DESC LIMIT 5;"
docker compose exec database psql -U course_rag -d course_rag -c \
  "SELECT course_id, physical_page_number, chunk_position, length(text) AS characters, vector_dims(embedding) AS dimensions FROM document_chunks ORDER BY created_at DESC LIMIT 20;"
```

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
