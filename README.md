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

Open the web app at <http://localhost:3000>. The readiness panel reports whether the API at <http://localhost:8000/api/v1/readiness> is reachable. PostgreSQL listens on port 5432. The checked-in defaults are for local development only; replace them before using this stack outside your machine. The API container applies Alembic migrations before starting, and its startup check verifies that the database vector schema matches the configured Gemini embedding model and dimensions. Gemini is not called at startup or by readiness, and no API key is needed until a live provider is invoked.

## Ask a course question

After ingesting PDFs, open <http://localhost:3000>, pick a scope (**All courses**, all courses of one subject, or a single course), enter a question, and select **Ask**. The course list comes from `GET /api/v1/courses`. The default local API uses deterministic providers, so the browser demo and automated checks do not require Gemini credentials. Deterministic mode matches questions to course text by shared words (accents and common French/English words are ignored) and answers with the best-matching excerpt; it has no semantic understanding. Use Gemini for real answers. A live provider is configured only on the server.

The API accepts `POST /api/v1/answers/stream` with JSON `{ "question": "..." }` plus an optional scope: `"course_id": "Algorithmique"` for one course or `"subject": "Informatique"` for every course in a subject. Without a scope it searches every course. It returns newline-delimited JSON (`application/x-ndjson`). Each event has `version: 1` and a `type`: supported answers emit one or more `delta` events, then `citations` with subject, course ID, document title, section title (may be `null`), filename, one-based physical page (slide number), and excerpt, then exactly one terminal `completed` event. When no course material is relevant enough, or the model finds that the retrieved material does not answer the question, the stream emits one terminal `abstention` event instead. Failures emit one safe terminal `error` event. `clarification` is reserved for later policy work. Answers are written in the language of the question.

Relevance uses a cosine-similarity cutoff, `EVIDENCE_MINIMUM_SCORE`. Leave it empty to use the provider default (0.6 for Gemini, measured on this course material: unrelated text scores about 0.5 and relevant chunks 0.7–0.8; 0.1 for deterministic), and tune it once real course material and test questions are available.

## Ingest course material

Course material is organized as **subject › course › document › page/section**. Put files beneath `data/course-pdfs/` (or set `PDF_SOURCE_DIR` in `.env`, for example `PDF_SOURCE_DIR=cours`) in either layout. Folder names become the subject and course names shown in the app, so use readable names; course names must be unique:

```text
cours/                              data/course-pdfs/
  Cryptographie/                      Informatique/          <- subject
    slides.pdf                          Algorithmique/       <- course
    TD/TD1.pdf                            cours_1.pdf
  Statistiques/                         Bases de données/
    fiche.md                              sql_intro.pdf
```

- A top-level folder that **directly contains files** is a course (filed under the subject `Général`); its subfolders only organize that course.
- A top-level folder containing **only folders** is a subject, and each of its folders is a course.

Supported formats are `.pdf`, `.md`/`.markdown`, `.txt`, and `.xlsx`. Other files (images, `.pptx`, `.docx`) and loose files outside a course folder are skipped with a warning; export PowerPoint and Word files to PDF. Check what will be stored before touching the database with a dry run, which needs no database and no API key:

```sh
uv run --project backend python -m app.ingest --all --dry-run              # summary + first 3 chunks per file
uv run --project backend python -m app.ingest --all --dry-run --show-chunks -1 --course-id Cryptographie
```

Then start the database and API (wait until the API is healthy, since it applies migrations on startup) and ingest everything, one subject, or one course:

```sh
docker compose up --build -d --wait database api
uv run --project backend python -m app.ingest --all
uv run --project backend python -m app.ingest --all --subject Informatique
uv run --project backend python -m app.ingest --all --course-id Algorithmique
```

### How files are parsed and chunked

**PDFs** are read with PyMuPDF, page by page, using font sizes, positions, and annotations:

- **Slides** (landscape pages) get one title: the largest text on the slide. **Documents** (portrait pages such as exercise sheets) are split at every heading, such as "Exercice 3", and text that continues on the next page keeps its heading. The first page's title becomes the document title; otherwise the filename is used.
- **Headers, footers, and page numbers** in the top/bottom margins that repeat on at least half the pages are removed.
- **Math stays readable**: exponents become `4^21`, subscripts `c_1`, and ligatures (`ﬁ`, `ﬀ`) plain letters.
- **Wrapped lines** are rejoined, words hyphenated at a line end are mended, and **bullets** stay on separate lines.
- **Beamer overlay steps** (the same slide revealed bullet by bullet) and pages repeated verbatim (a recurring outline) are dropped, keeping the complete version.
- **QCM pages** (numbered questions followed by lettered options) become one chunk per question, and options **highlighted** in the PDF are marked `✔ (réponse surlignée)`.
- **Section dividers**, meaning a "Chapitre 2 …" / "II. …" title with at most a short subtitle, or (when a deck has at least two) a title-only slide without a large picture, set the section of the slides that follow.

**Markdown and text** files are split at headings, keeping the heading path (for example `2. Mesures > Variance`); a single top `#` heading is the document title. Tables, lists, and formulas are kept verbatim.

**Excel workbooks** keep, per sheet, the rows that contain text (hypotheses, decision rules, labeled results such as `p = | 0.0018`); rows made only of numbers are counted, not embedded, because raw data carries no meaning on its own.

Chunks pack whole lines up to about 1,000 characters, so bullets and table rows are never cut; a chunk that starts inside a Markdown table repeats the table header. Before embedding, each chunk is prefixed with its place in the material, for example `Général > Cryptographie > Introduction à la cryptologie > RSA : un chiffrement asymétrique`, so terse slides are still found by course and topic. The model receives the same subject, course, document, and section with each excerpt, and sources show a page only for PDFs.

Scanned PDFs and picture-only slides have no extractable text; they are reported (pages without text are listed) and need OCR to be searchable. When parsing or chunking changes, `PARSER_VERSION` in `backend/app/ingestion.py` is bumped, and the next `--all` run rebuilds every document.

`--all` continues past a file that fails, reports it, and exits nonzero at the end. Re-running it is safe: unchanged files are skipped. When a file's contents change, re-ingesting it replaces the previous version of that file in the same course, so outdated text is no longer cited. Gemini embeddings are requested in batches of 25 chunks. On the free tier, `gemini-embedding-2` accepts 100 texts per minute, so a large import pauses when it reaches the limit, prints `Gemini rate limit reached; waiting 34s`, and continues; it stops with a clear message if the daily quota is used up (re-run `--all` later: finished files are skipped). Answering a question does not wait: if the quota is exhausted, the question fails with a safe error. Answers use `ANSWER_MODEL` and fall back, in order, to the comma-separated `ANSWER_FALLBACK_MODELS` when a model is overloaded (503) or rate limited (429); free-tier demand spikes can hit several models at once. Some listed models are not usable by new API keys (the Gemini 2.5 family returns 404), so check a model before configuring it.

To ingest a single file, name its course (and subject, which defaults to `Général`):

```sh
uv run --project backend python -m app.ingest --course-id Cryptographie --file Cryptographie/slides.pdf
```

Ingestion uses `RAG_PROVIDER` by default; `--provider deterministic` or `--provider gemini` can explicitly override it. Gemini requires `GEMINI_API_KEY`. Documents record which embedding provider created their vectors. API startup refuses retrieval if any document is unverified or belongs to another provider. Re-ingesting the same unchanged PDF with the configured provider safely replaces only its embeddings and provider marker in one transaction; failed embedding or persistence leaves the old vectors intact. A matching provider is a no-op.

The startup guard checks every document, so switching providers requires re-embedding every row whose marker differs from the selected provider (including legacy rows marked `NULL`). Before starting the API, configure `RAG_PROVIDER=gemini` and `GEMINI_API_KEY` in `.env`, start only PostgreSQL, apply migrations, and list all affected documents:

```sh
docker compose up -d database
uv run --project backend python -m alembic -c backend/alembic.ini upgrade head
docker compose exec database psql -U course_rag -d course_rag -c \
  "SELECT subject, course_id, source_filename, embedding_provider FROM documents WHERE embedding_provider IS DISTINCT FROM 'gemini' ORDER BY subject, course_id, source_filename;"
```

Make sure every returned PDF is still present beneath `PDF_SOURCE_DIR`, then re-embed them all with `uv run --project backend python -m app.ingest --all` (or one file with `--subject`, `--course-id`, and `--file` set to the returned `source_filename`). Repeat until the query returns no rows, then start the API and web app:

```sh
docker compose up --build -d api web
```

Do not delete or replace source files or database rows as a shortcut. If an affected PDF is missing, restore it before re-embedding. If its bytes have changed, ingesting the current file stores it as a new version and removes the old one. If the backend command is run outside the container, make sure `DATABASE_HOST=localhost` is set. If another PostgreSQL server already listens on port 5432 on your machine, commands run from the host connect to that server instead of the Compose database: stop it, or map the Compose database to another host port (for example `"5433:5432"` in `compose.yaml`) and set `DATABASE_PORT=5433` in `.env`. After startup, use the chat with a natural-language question about the re-embedded course. Deterministic mode remains the default and does not make external calls.

To inspect the saved page provenance and course ownership, query the local database:

```sh
docker compose exec database psql -U course_rag -d course_rag -c \
  "SELECT id, subject, course_id, title, source_filename, page_count FROM documents ORDER BY created_at DESC LIMIT 5;"
docker compose exec database psql -U course_rag -d course_rag -c \
  "SELECT course_id, physical_page_number, chunk_position, section_title, length(text) AS characters FROM document_chunks ORDER BY created_at DESC LIMIT 20;"
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
