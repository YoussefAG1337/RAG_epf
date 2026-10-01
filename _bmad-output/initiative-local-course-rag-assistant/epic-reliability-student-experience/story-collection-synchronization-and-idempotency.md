---
id: 1
type: story
title: "Collection synchronization and idempotency"
parent: epic-reliability-student-experience
covers: [CAP-1, CAP-7]
after: [6.3]
hitl: true
risk: high
---

# Collection synchronization and idempotency

## Description

Add deterministic directory discovery, reusable fixture PDFs, per-file validation and diagnostics, byte checksums, course-scoped unchanged-input no-ops, and inserted, updated, unchanged, skipped and failed reporting with nonzero incomplete-sync semantics.

## Acceptance Criteria

Verify: A mixed collection commits valid files independently, diagnoses invalid files and empty pages, exits nonzero when incomplete, and an unchanged rerun creates no duplicate writes or embedding calls while course identities remain isolated.

## References

- parent — _bmad-output/initiative-local-course-rag-assistant/epic-reliability-student-experience/epic-reliability-student-experience.md

## Notes

- Open question: The fixture corpus contains only source PDFs; evaluation questions and expected outcomes are deferred to Story 8.1.

## Plan

<!-- Filled in by the coding agent; never sent to a tracker. -->
