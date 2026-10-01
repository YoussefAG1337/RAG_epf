---
type: epic
title: "Reliability and student experience"
parent: initiative-local-course-rag-assistant
covers: [CAP-1, CAP-2, CAP-3, CAP-4, CAP-5, CAP-6, CAP-7]
after: []
assignee: ""
risk: high
---

# Reliability and student experience

## Description

Turn the tracer into a trustworthy course assistant by synchronizing the full collection safely, refusing unsafe answers before exposure, and completing the browser evidence and conversation experience.

## Outcome

Students can use the fixed course collection without stale data, cross-course leakage, unsupported claims, duplicate submissions, or opaque citations and failures.

## Requirements

- CAP-1: Complete collection discovery, diagnostics, checksums, unchanged-input idempotency, changed-file reconciliation, reporting, and failure semantics.
- CAP-2: Complete grounded-answer safety so generated content is exposed only after evidence and citation validation.
- CAP-3: Complete browser progress, conversation, duplication prevention, clarification, abstention, completion, and failure states.
- CAP-4: Enforce citation coverage for every substantive supported claim and preserve validated provenance to the browser.
- CAP-5: Display claim-linked filename, one-based physical page, and supporting excerpt cards in the chat flow.
- CAP-6: Clarify materially ambiguous questions when useful and otherwise return materials-insufficient abstention without exposing unsupported model-prior claims.
- CAP-7: Preserve course scope through collection identities, replacement, answer validation, and browser-visible evidence.

## Done when

1. A mixed PDF directory synchronizes valid files, diagnoses every invalid or empty input, and reruns unchanged without duplicate writes or embeddings.
2. Changed sources replace derived chunks transactionally, reuse retained embeddings, and preserve the previous searchable version on failure.
3. Unsupported, ambiguous, missing-citation, and wrong-course outputs expose no unsafe claim text and end in clarification or abstention as appropriate.
4. The chat prevents duplicate submission, retains the in-memory conversation, and shows honest progress, citations, clarification, abstention, completion, and errors.

## Boundaries

Reliability and complete student-facing behavior built on the tracer. Evaluation datasets, scoring records, and final cross-initiative hardening remain in Epic 8.

## References

- parent — _bmad-output/initiative-local-course-rag-assistant/initiative-local-course-rag-assistant.md
- spec — _bmad-output/specs/spec-local-course-rag-assistant/SPEC.md, CAP-1 through CAP-7 and Constraints
- implementation contract — _bmad-output/specs/spec-local-course-rag-assistant/stack.md, Ingestion behavior, Question-answer flow, API and UI contract, and Verification boundaries

## Notes

- Decision: tests, cleanup, and refactoring are part of each feature story; no routine epic refactor sweep is created, and final cross-initiative hardening is Story 8.3 (user direction, 2026-09-24).
- Decision: valid files commit independently; a failed requested source retains its previous valid version when available, is reported, and makes synchronization nonzero (approved 2026-09-24).
- Decision: partially text-bearing PDFs are accepted page by page with empty-page diagnostics; wholly text-empty or scanned PDFs are rejected (approved 2026-09-24).
- Decision: Story 7.1 supplies reusable source PDFs only; evaluation questions and records belong to Epic 8 (validated 2026-09-24).
- Decision: Story 7.3 withholds generated claims until grounding validation succeeds, so unsafe content is never streamed before abstention (validated 2026-09-24).

