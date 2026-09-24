---
id: SPEC-local-course-rag-assistant
companions:
  - stack.md
  - evaluation.md
sources: []
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate.

# Local Course RAG Assistant

## Why

Students need a trustworthy way to ask questions about one course without receiving plausible but ungrounded answers. The assistant must make a fixed PDF collection conversationally searchable while keeping every supported response auditable against the course materials.

## Capabilities

- **CAP-1**
  - **intent:** An operator can ingest the fixed course PDF collection through a local command so its contents become searchable.
  - **success:** An initial run creates page-linked searchable content, and rerunning unchanged inputs creates no duplicate documents or chunks.
- **CAP-2**
  - **intent:** A student can ask a course question and receive an answer based only on evidence retrieved from that course's ingested materials.
  - **success:** The answer contains no substantive claim that lacks support in retrieved course evidence.
- **CAP-3**
  - **intent:** A student can see an answer arrive incrementally in the chat interface.
  - **success:** Answer text streams before completion, then reaches an explicit completed or failed state without duplicating content.
- **CAP-4**
  - **intent:** A student can trace every supported answer to the PDF evidence used to produce it.
  - **success:** Each substantive supported claim maps to at least one citation containing the source filename and PDF page number.
- **CAP-5**
  - **intent:** A student can inspect the supporting excerpt for each citation without leaving the chat flow.
  - **success:** Every citation card displays its filename, page number, and the excerpt supporting the associated answer claim.
- **CAP-6**
  - **intent:** A student receives an explicit abstention when the course materials do not provide enough evidence to answer safely.
  - **success:** Unsupported, insufficiently supported, or materially ambiguous questions produce a materials-insufficient response and no answer derived from general model knowledge.
- **CAP-7**
  - **intent:** The system keeps documents, chunks, retrieval, and answers scoped to a course so additional courses can be added later without data leakage.
  - **success:** Every document and chunk has a `course_id`, and every retrieval request filters by the requested course before ranking evidence.
- **CAP-8**
  - **intent:** A developer can evaluate grounded-answer behavior against representative course questions.
  - **success:** A repeatable local evaluation covers factual, multi-passage, ambiguous, and unsupported cases; every deterministic check passes, and each semantic result is exposed for manual acceptance.

## Constraints

- The implementation contract and component boundaries are defined in `stack.md`; LangChain and LlamaIndex are prohibited.
- Answer generation may use only retrieved excerpts from the requested course as factual context; the system prompt must require abstention when that context is insufficient.
- Supported answers must expose filename and one-based physical PDF page number citations, with the cited excerpts available to the frontend; printed page labels are unsupported in V1.
- Ingestion must persist document checksums and be idempotent for unchanged files.
- Answer generation must default to `gemini-3.8-flash`; embeddings must use `gemini-embedding-2` with 768 output dimensions.
- Embedding model and output dimensionality are a coupled schema decision; changing either requires re-embedding all chunks and an appropriate database migration.
- V1 supports searchable, text-based PDFs only and a fixed collection for one course.
- The evaluation contract is defined in `evaluation.md`.

## Non-goals

- Authentication or user accounts.
- Instructor or course-administration interfaces.
- Deployment, production hosting, monitoring, or separate environments.
- OCR or support for scanned PDFs, audio, or video.
- Runtime upload, deletion, or management of course materials through the web interface.
- Cross-course search or a student-facing course selector in V1.

## Success signal

Against the evaluation dataset, students receive streamed, evidence-grounded answers with inspectable page citations for answerable questions, while ambiguous or unsupported questions abstain without introducing facts absent from the PDFs; rerunning ingestion leaves unchanged source data unduplicated.

## Assumptions

- Source PDFs contain extractable text and stable file-level page ordering suitable for `pypdf` extraction.
- The local ingestion command reads an explicitly configured directory and does not discover or mutate files outside it.
