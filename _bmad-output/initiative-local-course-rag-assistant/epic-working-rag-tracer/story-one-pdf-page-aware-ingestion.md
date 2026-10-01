---
id: 3
type: story
title: "One-PDF page-aware ingestion"
parent: epic-working-rag-tracer
covers: [CAP-1, CAP-7]
after: [2]
hitl: true
risk: high
status: done
---

# One-PDF page-aware ingestion

## Description

Add a validated local command that reads one explicitly selected PDF from the configured source directory without discovering or mutating content outside it, uses pypdf to extract usable text page by page, creates deterministic page-aware chunks, obtains controlled embeddings, and persists them under the requested course. Accept partially text-bearing PDFs while diagnosing empty pages, and reject a PDF only when it contains no usable searchable text.

## Acceptance Criteria

Verify: Ingesting a test PDF from the configured source directory produces inspectable persisted rows with stable chunk order, one-based physical-page provenance and 768-dimensional vectors; empty pages are diagnosed, a PDF with some usable text succeeds, a PDF with no usable text is rejected, paths outside the configured directory are not processed, and the same source ingested under two course IDs remains isolated.

## References

- parent — _bmad-output/initiative-local-course-rag-assistant/epic-working-rag-tracer/epic-working-rag-tracer.md
- requirements — _bmad-output/specs/spec-local-course-rag-assistant/SPEC.md, CAP-1 and CAP-7
- implementation contract — _bmad-output/specs/spec-local-course-rag-assistant/stack.md, ingestion and data sections

## Notes

- Open question: Unchanged rerun and collection synchronization behavior are intentionally deferred to Story 7.1.

## Plan

<!-- Filled in by the coding agent; never sent to a tracker. -->
