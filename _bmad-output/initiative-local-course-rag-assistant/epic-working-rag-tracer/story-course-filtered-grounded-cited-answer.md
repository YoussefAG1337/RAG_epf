---
id: 4
type: story
title: "Course-filtered grounded cited answer"
parent: epic-working-rag-tracer
covers: [CAP-2, CAP-4, CAP-7]
after: [3]
risk: high
status: done
---

# Course-filtered grounded cited answer

## Description

Embed a question, filter pgvector retrieval by course before ranking, preserve provenance, call gemini-3.8-flash through the controlled direct boundary using only the question, grounding instructions and selected excerpts, and validate structured claim citation IDs.

## Acceptance Criteria

Verify: A closer wrong-course vector is excluded, the provider receives no other factual context, and every substantive supported claim resolves to same-course filename, physical page, and excerpt evidence.

## References

- parent — _bmad-output/initiative-local-course-rag-assistant/epic-working-rag-tracer/epic-working-rag-tracer.md

## Notes

- Open question: Insufficient and ambiguous evidence policy is intentionally deferred to Story 7.3.

## Plan

<!-- Filled in by the coding agent; never sent to a tracker. -->
