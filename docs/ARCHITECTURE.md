# Architecture and design decisions

This document explains, step by step, how the course assistant works and **why** each part is built the way it is. The [README](../README.md) covers what the project does and how to run it; this document is for readers who want to understand or evaluate the design.

Contents:

1. [Goal and requirements](#1-goal-and-requirements)
2. [System overview](#2-system-overview)
3. [How the project was built (steps)](#3-how-the-project-was-built-steps)
4. [Course material and ingestion](#4-course-material-and-ingestion)
5. [Embedding model](#5-embedding-model)
6. [Vector database](#6-vector-database)
7. [Retrieval](#7-retrieval)
8. [Answer generation and the LLM prompt](#8-answer-generation-and-the-llm-prompt)
9. [Citations and exact highlighting](#9-citations-and-exact-highlighting)
10. [Conversations and follow-up questions](#10-conversations-and-follow-up-questions)
11. [API](#11-api)
12. [User interface](#12-user-interface)
13. [Deployment](#13-deployment)
14. [Evaluation and testing](#14-evaluation-and-testing)
15. [Limitations and future work](#15-limitations-and-future-work)
16. [Decision summary](#16-decision-summary)

---

## 1. Goal and requirements

Students ask questions about their courses and must get answers they can **trust and verify**. That translates into requirements:

| Requirement | Consequence in the design |
|---|---|
| Answers come only from the course material, never from the model's general knowledge | Retrieval-augmented generation (RAG) with a strict prompt, and a validation layer that drops anything not backed by a retrieved excerpt |
| Every statement can be verified | Each claim cites excerpts with a verbatim quote; clicking a citation opens the original document at the exact lines |
| "I don't know" instead of guessing | A relevance cutoff before generation, and an explicit empty answer the model must return when excerpts do not answer |
| Works on real course material: Beamer slides, PowerPoint exports, exercise sheets, QCMs, Markdown summaries, spreadsheets | Format-specific parsing with layout heuristics (section 4) |
| French and English | A multilingual embedding model; answers in the language of the question |
| Questions scoped to a subject, a course, or one document | Scope filters applied in SQL before ranking |
| Follow-up questions and saved conversations | Conversation storage and question rewriting |
| Free to run for students | Local embeddings (no quota); answers on Gemini's free tier with fallbacks |

## 2. System overview

```text
                 ┌──────────────────── import (python -m app.ingest) ───────────────────┐
 cours/*.pdf,    │ parse (PyMuPDF /   chunk by heading   locate each line   embed chunk  │
 .md, .txt,  ───►│ Markdown / Excel) ─► and line, ≤1000 ─► in the source  ─► with its    ├──► PostgreSQL
 .xlsx           │                      characters         (page, box)       context     │    + pgvector
                 └──────────────────────────────────────────────────────────────────────┘

 question ──► rewrite follow-up ──► embed ──► top 8 chunks in scope ──► cutoff 0.40 ──► Gemini:
 (web UI)     using the history     question   (cosine, HNSW)           (or "not found")  claims + quotes
                                                                                              │
 viewer with highlighted lines ◄── citations with boxes ◄── verify quotes ◄── drop invalid ◄──┘
```

Four services:

| Service | Technology | Role |
|---|---|---|
| Embeddings | Python, sentence-transformers, PyTorch (Metal/CUDA/CPU) | Serves the Qwen3-Embedding-0.6B model over HTTP (`POST /embed`) |
| Database | PostgreSQL 16 + pgvector | Documents, chunks with vectors and source locations, conversations |
| API | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PyMuPDF | Ingestion, retrieval, answering, conversations, document files |
| Web | Next.js 15, React 19, Tailwind CSS 4, react-pdf (pdf.js), KaTeX | French interface: library, chat, document viewer |

## 3. How the project was built (steps)

The project went through these stages; each fixed a problem found by testing the previous one.

1. **Initial scaffold.** FastAPI + Next.js + PostgreSQL/pgvector, single-PDF ingestion with `pypdf`, Gemini embeddings, answers with citations to a page, and an offline "deterministic" mode for tests. Testing showed the offline mode could never find anything (its vectors were random hashes), refusals were shown as errors, and the user had to type an exact course ID.
2. **Usable pipeline.** Lexical offline vectors, a proper "not found" answer, a course list, batch ingestion, replacement of changed files, answers in the question's language.
3. **Course hierarchy and slide-aware parsing.** Subject › course › document; PyMuPDF instead of `pypdf` to read font sizes and positions (slide titles, headers/footers); chunks carry their place in the course when embedded.
4. **Real material.** The actual EPF files (Cryptographie, Statistiques, Virtualisation) exposed Beamer overlays, lost exponents (`4^21` read as `421`), ligatures, exercise headings in the middle of pages, QCMs with highlighted answers, Markdown and Excel files. Each got a dedicated rule (section 4).
5. **Gemini answering and robustness.** Gemini's free tier imposed quotas and overload errors: batched embeddings, fallback models, measured relevance cutoff.
6. **Local embeddings.** To remove the embedding quota, candidate local models were benchmarked on the course material and Qwen3-Embedding-0.6B was selected (section 5). It then moved from Docker to native execution to use the Apple GPU (11× faster).
7. **Exact citations and the new interface.** Source line locations at ingestion, verified verbatim quotes, highlight boxes, a PDF viewer, document-scoped questions, saved conversations, follow-up rewriting, streaming — then verified end to end in a real browser.

## 4. Course material and ingestion

Command: `python -m app.ingest --all` (`--dry-run` shows the chunks without storing anything). Code: `backend/app/ingestion.py`, `pdf_parsing.py`, `text_parsing.py`, `locating.py`.

### 4.1 Folder layout and hierarchy

Courses are folders: `cours/<course>/files` or `cours/<subject>/<course>/files`. A top-level folder that contains files directly is a course (filed under subject "Général"); one that contains only folders is a subject.

*Why folders:* teachers and students already organize files this way; it needs no database editing, and the folder names become the names shown in the app. *Why a hierarchy:* questions often concern one course, and scoping retrieval to it removes unrelated but similar passages (for example "chiffrement" appears in both cryptography and virtualisation).

### 4.2 Parsing PDFs (PyMuPDF)

`pypdf` returns plain text only. PyMuPDF returns every line with its font size, position, and style, plus annotations (highlights). The heuristics below were each added because a real course file needed them:

| Problem observed | Rule | Threshold / detail |
|---|---|---|
| Slide titles must be known (they name the topic) | On landscape pages (slides), the title is the **largest** text on the page, with the lines of the same text box | Must be ≥ 1.2× the document's body font size |
| Exercise sheets have headings mid-page ("Exercice 3") | On portrait pages, every heading-sized line starts a new segment; text continuing on the next page keeps the heading | Same 1.2× ratio |
| Body text size must not be skewed by titles | Body size = font size covering the most characters, ignoring each page's top line | — |
| Repeated headers/footers ("EPF – Algorithmique 2025 – 3/40") pollute every chunk | Margin lines no larger than body text, repeated on at least half the pages, are removed; page numbers too | Top/bottom 12 % of the page; ≥ 50 % of pages and ≥ 2 |
| Titles like "Exercice 1", "Exercice 2" must not be removed as "repeated" | Numbers are ignored when comparing only for small print (footers); body-sized text must repeat exactly | — |
| Exponents lost: `4^21 mod 493` extracted as `421 mod 493` | Superscripts become `^x`, subscripts `_x`, attached to the base | PyMuPDF superscript flag, or smaller text raised/lowered by ≥ 10 % of the font size |
| Ligatures: "chiﬀrement" does not match "chiffrement" | `ﬀ ﬁ ﬂ ﬃ ﬄ ﬅ ﬆ` replaced by plain letters | — |
| Beamer overlays: 211 pages for 116 slides (the same slide revealed bullet by bullet) | A page followed by one with the same title whose content only grows (or whose body is nearly empty) is dropped; pages repeated verbatim (the outline before each section) too | 211 → 114 pages kept |
| Wrapped lines and hyphenation ("struc-" / "ture") | Lines of the same text block are rejoined; a hyphen at a line end followed by a lowercase letter is removed; bullets stay on separate lines | Bullet glyphs (•, ▶, Wingdings…) normalized to `- ` |
| QCMs: one page, many questions | A page with ≥ 3 numbered questions followed by lettered options is split into one segment per question | — |
| QCM correct answers are **highlighted in yellow** in the PDF | Highlight annotations overlapping an option line mark it `✔ (réponse surlignée)` | ≥ 50 % vertical and ≥ 30 % horizontal overlap |
| Section dividers ("Chapitre 2 : Les arbres") give context to the following slides | A "Chapitre/Partie/II."-style title with at most a short subtitle, or a title-only page without a large picture when the deck has at least two, sets the section | Subtitle ≤ 60 characters; picture < 10 % of the page |
| A title-only slide that is a diagram is not a divider | Pages mostly covered by an image are content | — |

### 4.3 Parsing Markdown, text, and Excel

- **Markdown / text**: split at headings, keeping the heading path (`7. Tests d'hypothèses > P-value`); a single top `#` heading is the document title; tables, lists, and LaTeX formulas are kept as written.
- **Excel**: per sheet, rows containing text are kept (hypotheses, decision rules, labelled results like `p = | 0.0018`); rows of numbers only are counted but not embedded, because a row like `112 | 111` carries no meaning on its own and would only add noise to retrieval.
- **Other formats** (`.pptx`, `.docx`, images) are skipped with a warning: exporting to PDF preserves the layout that the PDF rules rely on.

### 4.4 Chunking

Each segment (a slide, an exercise, a QCM question, a Markdown section, a sheet) is split into chunks by **packing whole lines** up to about **1,000 characters**, with up to **150 characters** of trailing lines repeated in the next chunk.

*Why segments first:* a slide or an exercise is a natural unit of meaning; cutting by a fixed number of characters would mix the end of one topic with the start of another. *Why ~1,000 characters (~250 tokens):* long enough to hold a slide or an exercise with its context, short enough that one chunk is about one idea (a precise embedding) and that 8 chunks fit comfortably in the prompt. *Why whole lines:* a bullet or a table row cut in half loses its meaning. *Why overlap:* a sentence that spans two chunks stays findable from both. A chunk that starts inside a Markdown table repeats the table's header rows, so its columns stay interpretable.

### 4.5 Context added before embedding

A slide often says only "Insertion : O(1)". Before embedding, each chunk is prefixed with its place in the material:

```text
Général > Cryptographie > Introduction à la cryptologie > RSA : Rivest Shamir Adleman

Bob choisit deux grands nombres premiers p et q et un entier e premier avec (p −1)(q −1). …
```

*Why:* the vector then represents both the content and the topic, so "insertion dans une liste chaînée" finds the terse slide. The stored text (shown to users and to the LLM) does not include the prefix; the LLM receives the same information as separate fields.

### 4.6 Source locations

For exact highlighting, each chunk stores where its lines are in the original file: page and bounding box (as fractions of the page) for PDFs, line numbers for text files, rows for spreadsheets. Because parsing rewrites text (bullets, joined lines, mended hyphens), offsets cannot be carried through; instead both the chunk and each source line are reduced to a **skeleton** (lowercase letters and digits, accents removed) and matched in order. Lines shorter than 3 skeleton characters (diagram labels like "m") are skipped because they would match anywhere. On the course material, 364 of 364 chunks have locations.

### 4.7 Safe re-imports

- A document's identity is derived from its subject, course, path, SHA-256 checksum, and parser version: re-running an import skips unchanged files.
- A changed file replaces its previous version in the same transaction, so outdated text is never cited.
- Changing the parsing rules bumps `PARSER_VERSION`, which makes the next import rebuild everything.
- Each document records which embedding provider produced its vectors; the API refuses to start if they do not match the configured model, instead of silently comparing incompatible vectors.

## 5. Embedding model

**Choice: [Qwen/Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)**, 1024-dimensional vectors, run locally.

### 5.1 Why local instead of an API

The first version used Gemini's embedding API. On the free tier it accepts 100 texts per minute; importing the courses (364 chunks) had to pause repeatedly, and every student question also consumed quota. A local model has no quota, no cost, keeps course material on the machine, and — measured below — is fast enough.

### 5.2 How the model was selected

Candidates were chosen among open multilingual retrieval models that run on a laptop. Each was evaluated on **30 real questions about the course material** (French and English, including paraphrases such as "migrer une machine virtuelle à chaud" for vMotion), each with the passages that answer it, plus off-topic questions:

| Model | Size | Right passage 1st | in top 3 | in top 8 | MRR | Off-topic best score / typical match |
|---|---|---|---|---|---|---|
| intfloat/multilingual-e5-base | 278 M | 87 % | 93 % | 93 % | 0.90 | 0.82 / 0.86 |
| **Qwen/Qwen3-Embedding-0.6B** | 600 M | 83 % | **97 %** | **100 %** | **0.91** | **0.33 / 0.74** |
| BAAI/bge-m3 | 568 M | 83 % | 90 % | 93 % | 0.88 | 0.41 / 0.62 |

(Two other candidates, multilingual-e5-large-instruct and EmbeddingGemma-300m, were dropped: the first is in bge-m3's class and as large to download; the second requires an authenticated licence acceptance.)

*Why Qwen3:*
- **The right passage is always among the 8 excerpts** the answer model reads (100 % at 8). Being first matters less than being included, because the LLM reads all 8.
- **Clear separation of relevant and off-topic questions** (0.33 vs 0.74): this is what makes "not found" reliable. e5-base, despite a good ranking, scores off-topic questions almost as high as real ones (0.82 vs 0.86), so it could not tell them apart.
- Its misses are near misses (the RSA exercise ranked above the RSA slide, both relevant).

### 5.3 Questions and passages are encoded differently

Retrieval models are trained with an instruction on the question side. The embedding service applies each model's convention (for Qwen3, the `query` prompt on questions, none on passages), so the rest of the system only says whether it sends a question or a passage.

### 5.4 Where it runs, and number precision

Measured on an Apple M4 Pro, embedding the 364 chunks:

| Configuration | Time |
|---|---|
| Docker (Linux VM, CPU) | ~350 s |
| macOS, CPU, bfloat16 (the model's stored format) | ~1,600 s |
| macOS, CPU, float32 | 62 s |
| **macOS, Apple GPU (Metal), float16, batch 8** | **31 s** |

- Docker on macOS runs a Linux virtual machine without GPU access, so the embedding service runs **natively** (`start.sh`) while the other services stay in Docker; the API reaches it through `host.docker.internal`.
- CPUs compute bfloat16 about 30× slower than float32, so the CPU path forces float32. GPUs use float16: the vectors match float32 (cosine ≥ 0.9998).
- Smaller batches pad less: batch 8 was fastest on the GPU (31 s vs 50 s at 64).
- A question takes about 0.1 s to embed either way.
- `EMBEDDINGS_IN_DOCKER=1 ./start.sh` keeps a fully containerized option for machines without `uv` or a usable GPU.

## 6. Vector database

**Choice: PostgreSQL 16 with the pgvector extension**, HNSW index on cosine distance.

*Why PostgreSQL rather than a dedicated vector database (Qdrant, Chroma, Pinecone, Weaviate):*
- The data is relational as much as vectorial: subjects, courses, documents, chunks, conversations, messages. One database keeps them consistent with foreign keys and transactions (a changed file's old chunks are deleted and new ones inserted atomically).
- **Scope filters are plain SQL** (`WHERE course_id = …`) applied in the same query as the vector ranking, before the limit — so a course-scoped question can never be answered from another course.
- The corpus is small (hundreds to thousands of chunks); pgvector's HNSW index is more than fast enough, and one fewer service is simpler to run and to explain.
- Pinecone would add an external cloud dependency; Chroma/Qdrant would duplicate document metadata in a second store.

### 6.1 Schema

| Table | Content |
|---|---|
| `documents` | id, subject, course, title, source path, SHA-256 checksum, page count, embedding provider |
| `document_chunks` | id, document, course, page, position, section path, text, **locations** (JSONB), **embedding** (`vector(1024)`) |
| `embedding_schema_metadata` | the model name and vector size the column was built for |
| `conversations` | id, title, scope (JSONB), timestamps |
| `messages` | conversation, position, role, content, payload (JSONB: claims, citations, status) |

The index `ix_chunks_embedding_hnsw` uses `vector_cosine_ops`. Cosine similarity is used because the model's vectors are normalized and trained for it. Migrations are managed with Alembic (`backend/alembic/versions`); at startup the API checks that the database was built for the configured model and vector size.

## 7. Retrieval

1. **Follow-up rewriting** (section 10): "Et pour AES ?" becomes "Comment fonctionne le chiffrement AES ?" before searching.
2. **Embed the question** with the query convention.
3. **Search in scope**: the SQL query filters by subject, course, or document, orders by cosine distance using the HNSW index, and keeps the **8 best chunks**.
4. **Relevance cutoff 0.40**: chunks below it are discarded; if none remain, the answer is "Je n'ai pas trouvé cette information dans les supports de cours" and no LLM call is made.

*Why 8 chunks:* measured — the right passage is within the top 8 for 100 % of the test questions (top 3: 97 %). Fewer would miss some; more would dilute the prompt and cost tokens.

*Why 0.40:* measured with `python -m app.evaluate`. The right passages of answerable questions score at least 0.447 (the lowest being English questions about French slides), while 4 of 5 off-topic questions score at most 0.35. The fifth ("Quel temps fera-t-il demain à Paris ?", 0.452) matched a spreadsheet of flight data; it passes the cutoff but the LLM then refuses it (section 8). The cutoff is therefore set to never lose an answerable question, and the LLM is the second safeguard.

## 8. Answer generation and the LLM prompt

**Model: Gemini 3.8 Flash**, with fallbacks `gemini-3.7-flash`, `gemini-3.6-flash`, `gemini-3.5-flash-lite`.

*Why Gemini Flash:* free tier available to students, fast, good in French, supports JSON output and streaming. *Why fallbacks:* on the free tier, models regularly answer "503 high demand" or "429 rate limited"; the next model is tried automatically. Models were chosen by testing which ones the API key can actually use (the Gemini 2.5 family, although listed, returns "not available to new users"). A model that accepts a request but sends nothing for 30 seconds is also skipped: end-to-end testing caught the free tier leaving a stream open indefinitely, which would otherwise leave the student waiting forever. When all models are busy or stalled, the user sees a specific "service saturé, réessayez dans une minute" message.

### 8.1 The prompt

The model receives (in French, since the course material and users are French-speaking):

```text
Tu es l'assistant de cours d'une école d'ingénieurs. Réponds à la question de l'étudiant
uniquement avec les informations des extraits de cours ci-dessous (diapositives, TD, QCM,
fiches). Les diapositives sont concises : relie les extraits en phrases complètes et
claires, sans ajouter de fait absent des extraits.
Donne une réponse complète, en 2 à 6 affirmations quand les extraits le permettent, en
combinant tous les extraits pertinents. Pour expliquer une notion, appuie-toi d'abord sur
les cours et les fiches : un énoncé d'exercice (TD) pose une question, il ne l'explique
pas ; cite-le quand l'étudiant demande un exercice. Une réponse de QCM marquée
« ✔ (réponse surlignée) » est la bonne réponse.
Réponds en JSON avec exactement cette forme :
{"claims":[{"text":"une ou deux phrases","sources":[{"id":"S1","quote":"passage copié mot
pour mot de l'extrait S1 (5 à 25 mots)"}]}]}.
Chaque affirmation cite au moins un extrait ; la citation (quote) doit être recopiée
exactement depuis cet extrait. N'invente ni fait, ni source, ni citation. Si les extraits
ne permettent pas de répondre, renvoie {"claims":[]}. Écris les affirmations dans la
langue de la question.

[Conversation précédente (pour comprendre la question, pas comme source de faits) : …]
Question :
…
Extraits :
[{"id":"S1","cours":"Cryptographie","document":"Introduction à la cryptologie",
  "section":"RSA : Rivest Shamir Adleman","page":52,"extrait":"…"}, …]
```

Why each instruction is there:

| Instruction | Reason |
|---|---|
| "uniquement avec les informations des extraits" / "N'invente ni fait…" | The core RAG rule: answers must be verifiable against the course, not the model's general knowledge. |
| "Les diapositives sont concises : relie les extraits en phrases complètes" | Slides are bullet fragments; without this the model either copies fragments or refuses because no single slide "explains". It may connect excerpts, but not add facts. |
| "2 à 6 affirmations … en combinant tous les extraits pertinents" | Testing showed one-sentence answers built from a single excerpt even when several were relevant. |
| "appuie-toi d'abord sur les cours et les fiches : un énoncé d'exercice (TD) pose une question" | Testing showed "Comment fonctionne RSA ?" answered from an exercise statement's setup rather than the lecture slide. Exercises are still cited when the student asks for exercises. |
| "Une réponse de QCM marquée « ✔ (réponse surlignée) » est la bonne réponse" | The parser marks highlighted QCM answers; the model must know what the marker means. |
| JSON `claims` with `sources` | Makes every statement machine-checkable: the server can validate each citation instead of trusting prose. |
| Short labels `S1…S8` instead of database IDs | Long UUIDs are copied incorrectly by LLMs; short labels are reliable and mapped back on the server. |
| A verbatim `quote` of 5–25 words per source | Lets the server verify that the excerpt really supports the claim and locate the exact lines to highlight. 5–25 words is long enough to be specific, short enough to be copied exactly. |
| `{"claims":[]}` when the excerpts do not answer | An explicit, checkable way to say "not found", instead of an evasive answer. |
| "dans la langue de la question" | Questions come in French and English; the material is mostly French. |
| Excerpts include course, document, section, page | Lets the model tell sources apart (a slide vs. an exercise, two courses) and write accurate sentences. |
| History "pas comme source de faits" | Previous answers help interpret the question but must not become an unverified source. |

### 8.2 Validation of the model's output

The server never shows the model's text directly:

- Unknown source labels are dropped; a claim left without a valid source is **not shown**.
- Each quote is searched in its excerpt (exactly on the skeleton, otherwise fuzzy matching with a similarity ≥ 85/100 via `rapidfuzz`). Verified quotes become highlights; an unverifiable quote still counts as a citation of the excerpt, which is then highlighted entirely.
- Citations are numbered by first use, so markers read [1], [2], … in the order the student meets them.
- An empty `claims` list, or no valid claim, becomes the "not found" answer.

### 8.3 Streaming

Gemini's JSON is streamed; a small incremental parser extracts each complete claim object as soon as it closes (handling braces inside strings), validates it, and sends it to the browser. The student sees the answer sentence by sentence instead of waiting for the whole response; citations (which need all claims) are sent at the end.

## 9. Citations and exact highlighting

Pipeline: the quote's position in the chunk (section 8.2) is intersected with the chunk's stored line locations (section 4.6), giving page-by-page boxes (PDF) or line/row numbers (text, spreadsheets). The viewer draws those boxes over the rendered page and scrolls them into view. In the evaluation, **41 of 41 citations had a verified quote**, so every citation highlighted the exact lines rather than the whole excerpt.

## 10. Conversations and follow-up questions

- Each question and answer (with its claims, citations, and status) is stored in `messages`; the conversation keeps its scope. Conversations are listed, renamed, and deleted from the interface; the URL (`?c=…&doc=…&p=…`) reopens a conversation and an open document after a reload.
- **Follow-up rewriting**: with a history, the last 6 messages and the new question are sent to the model with an instruction to rewrite the question so it is understandable alone ("Et pour signer un message ?" → "Comment fait-on pour signer un message avec le système RSA ?"). The rewritten question is used **for retrieval**; the original question and the history are given to the answer prompt. *Why:* the vector of "Et pour AES ?" does not contain "chiffrement"; searching with it retrieves nothing useful. The interface shows the rewritten search ("Recherche effectuée : …") so students can see how they were understood.

## 11. API

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/courses`, `GET /api/v1/documents` | Library |
| `GET /api/v1/documents/{id}`, `/file`, `/content` | Document metadata, original file (path-checked), line-numbered text or sheet rows |
| `GET/PATCH/DELETE /api/v1/conversations[/{id}]` | Saved conversations |
| `POST /api/v1/answers/stream` | Answer a question: `{question, conversation_id?, scope: {subject?, course_id?, document_id?}}` |

The answer stream is newline-delimited JSON (contract version 2): `conversation`, `status` (searching, writing), one `claim` per sentence, `citations`, then `completed` — or a single `abstention` or `error`. Errors shown to users are generic and in French; details are logged server-side only.

## 12. User interface

French, three panes:

- **Library and conversations** (left): choose the scope (all courses, a subject, a course), open a document, reopen a conversation.
- **Chat** (centre): answers with numbered markers after each sentence; hovering shows the verified quote; source cards list document, course, page, and quote. A chip above the input shows the current scope.
- **Document viewer** (right): PDFs rendered with **react-pdf (pdf.js)** with an overlay of highlight boxes, page navigation, and zoom; Markdown and text rendered as formatted blocks (tables, **KaTeX** formulas) with the cited blocks highlighted; spreadsheets as tables with the cited rows highlighted. "Poser une question sur ce document" scopes the chat to it.

*Why react-pdf:* it renders the real PDF page in the browser, so highlights can be drawn at exact positions; the alternative (an `<iframe>` with the browser's PDF viewer) can jump to a page but cannot highlight. *Why scrolling inside the viewer's own container:* in Chromium, two concurrent smooth `scrollIntoView` calls (the chat following a new answer and the viewer jumping to a citation) cancel each other, which left the viewer at the top of the document.

## 13. Deployment

- `./start.sh` starts the embedding service natively (GPU), waits until the model is loaded, then `docker compose up` starts PostgreSQL, the API (which applies database migrations on startup), and the web app (a production Next.js build). `./stop.sh` stops everything; data persists in a Docker volume.
- Course files are mounted read-only into the API container so the viewer can serve originals.
- Secrets (the Gemini key) live in `.env`, which is git-ignored; course material (`cours/`) is git-ignored too.

## 14. Evaluation and testing

**Retrieval** (`python -m app.evaluate`, 30 questions): right passage first 83 %, in the top 3 97 %, in the top 8 100 %, MRR 0.908.

**Answers** (`--answers`, same questions + 5 off-topic): 28 of 30 answerable questions answered; all 5 off-topic questions refused; 41 of 41 citations with a verified quote. The two misses: a question whose only material is spreadsheet rows (the model refused rather than extrapolate) and one generation that failed when every Gemini model was busy.

**Automated tests**:
- Backend: 112 tests (parsing on generated PDFs, chunking, locating, quote verification, answering, API, providers), including integration tests that create a throwaway PostgreSQL database, apply the migrations, import a document, and query it.
- Frontend: 24 tests (stream contract validation, chat state, inline math rendering, the workspace flows).
- Browser: an end-to-end script drives a real Chromium browser through the interface (ask, open a citation and check the highlight boxes and the panel layout, follow-up, reload, document scope, Markdown citation visible, off-topic refusal, no console errors) — 12/12 checks.
- Lint and type checks: ruff, mypy (strict), ESLint, TypeScript.

## 15. Limitations and future work

- **Images are not read**: diagram-only slides contribute only their title; scanned PDFs need OCR. A vision model could describe diagrams at import time.
- **Spreadsheets** are indexed only by their text rows; numerical data is not queried.
- **Single user**: no authentication; all conversations are shared on one machine.
- **Free tier**: answer generation depends on Gemini's quotas; heavy use needs a paid key or a local LLM.
- **Evaluation size**: 30 questions give a useful signal but not statistical certainty; the question set should grow with the courses (`backend/eval/questions.json`).
- **Keyword search**: a hybrid search (BM25 + vectors) could help exact terms such as course codes.

## 16. Decision summary

| Decision | Chosen | Alternatives considered | Main reason |
|---|---|---|---|
| Architecture | RAG with verified citations | Fine-tuning; plain chat | Answers must be traceable to the course; material changes each semester |
| PDF parsing | PyMuPDF with layout rules | pypdf; OCR | Needs font sizes, positions, highlights |
| Chunk unit | Slide / exercise / question / section, ≤ 1,000 chars, whole lines | Fixed-size windows | Keeps one idea per chunk; never cuts bullets or rows |
| Embedding context | Subject > course > document > section prefix | Raw text | Terse slides become findable |
| Embedding model | Qwen3-Embedding-0.6B, local | Gemini API, bge-m3, e5 | Best retrieval on the material, clean relevance gap, no quota |
| Embedding runtime | Native, GPU, float16 | Docker CPU | 11× faster (31 s vs ~350 s) |
| Vector database | PostgreSQL + pgvector (HNSW, cosine) | Qdrant, Chroma, Pinecone | One store for relational data and vectors; scope filters in SQL |
| Excerpts per question | 8 | 3–20 | 100 % of right passages within 8 |
| Relevance cutoff | 0.40 | 0.35–0.60 | Measured: keeps every answerable question; LLM refuses the rest |
| Answer model | Gemini 3.8 Flash + fallbacks | Larger models, local LLM | Free tier, speed, French quality, JSON + streaming |
| Answer format | JSON claims with labelled sources and verbatim quotes | Free text with [1] markers | Server-side verification and exact highlighting |
| Follow-ups | Rewrite into a standalone question before searching | Search with the raw question | Pronouns and ellipses carry no searchable meaning |
| PDF viewer | react-pdf with an overlay | Browser PDF in an iframe | Only way to draw highlights at exact positions |
| Interface language | French | English | Users and material are French-speaking |
