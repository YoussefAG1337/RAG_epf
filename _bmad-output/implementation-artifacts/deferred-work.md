- source_spec: `_bmad-output/implementation-artifacts/spec-6-3-one-pdf-page-aware-ingestion.md`
  summary: Add a PostgreSQL-backed failure-path integration test proving document and chunk writes roll back together when chunk persistence fails.
  evidence: The current persistence-failure test uses a fake transaction context; local PostgreSQL smoke verification covered successful writes and outer-transaction rollback, not a failed flush after a document insert.
