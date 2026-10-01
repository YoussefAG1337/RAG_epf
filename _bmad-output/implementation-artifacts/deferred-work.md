- source_spec: `_bmad-output/implementation-artifacts/spec-6-3-one-pdf-page-aware-ingestion.md`
  summary: Add a PostgreSQL-backed failure-path integration test proving document and chunk writes roll back together when chunk persistence fails.
  evidence: The current persistence-failure test uses a fake transaction context; local PostgreSQL smoke verification covered successful writes and outer-transaction rollback, not a failed flush after a document insert.

- source_spec: `_bmad-output/implementation-artifacts/spec-6-5-streaming-endpoint-and-basic-browser-chat.md`
  summary: Revisit app-scoped SQLAlchemy engine/session-factory lifecycle for sustained answer-stream API use.
  evidence: `POST /api/v1/answers/stream` creates and disposes an engine per request when no session factory is injected; current story demonstrates the local path and fake-session tests.
- source_spec: `_bmad-output/implementation-artifacts/spec-6-5-streaming-endpoint-and-basic-browser-chat.md`
  summary: Strengthen semantic claim-to-evidence support validation in the existing answer-generation path.
  evidence: `backend/app/answering.py` checks citation identifiers and course provenance; reviewer questioned whether cited evidence semantically supports each generated claim. This story is constrained to reuse answer-generation semantics unchanged.
- source_spec: `_bmad-output/implementation-artifacts/spec-6-5-streaming-endpoint-and-basic-browser-chat.md`
  summary: Map retrieval database exceptions to a typed safe answer-path failure.
  evidence: `backend/app/answering.py` may propagate retrieval database errors outside its answer error wrapper; this predates streaming and remains outside this story's scope.
- source_spec: `_bmad-output/implementation-artifacts/spec-6-5-streaming-endpoint-and-basic-browser-chat.md`
  summary: Add a deterministic browser-to-API integration smoke test across the running local stack.
  evidence: Frontend API-client tests assert the POST path/body and parse NDJSON; backend tests verify emitted NDJSON. No single test drives the browser through the running API, and ordinary story tests must remain offline.
