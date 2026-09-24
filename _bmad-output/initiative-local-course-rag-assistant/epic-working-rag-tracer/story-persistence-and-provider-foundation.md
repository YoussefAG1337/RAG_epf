---
id: 2
type: story
title: "Persistence and provider foundation"
parent: epic-working-rag-tracer
covers: [CAP-7, T1]
after: [1]
risk: high
---

# Persistence and provider foundation

## Description

Add the course-scoped document/chunk vector schema and migrations, semantic runtime configuration validation, direct thin Gemini embedding and generation interfaces, deterministic doubles, and the model-dimension schema guard.

## Acceptance Criteria

Verify: A clean migration exposes every required constraint, index, provenance and vector(768) field, offline provider tests pass, secrets remain server-side, and incompatible embedding configuration prevents startup.

## References

- parent — _bmad-output/initiative-local-course-rag-assistant/epic-working-rag-tracer/epic-working-rag-tracer.md

## Notes

- Open question: None; model identifiers and the 768-dimensional schema are fixed by the specification.

## Plan

<!-- Filled in by the coding agent; never sent to a tracker. -->
