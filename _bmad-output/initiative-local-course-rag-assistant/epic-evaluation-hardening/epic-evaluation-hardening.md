---
type: epic
title: "Evaluation and hardening"
parent: initiative-local-course-rag-assistant
covers: [CAP-8]
after: []
assignee: ""
risk: high
---

# Evaluation and hardening

## Description

Make grounded behavior repeatably assessable, then verify and harden the complete local product through one final cross-initiative pass.

## Outcome

Developers can inspect deterministic and semantic evaluation results, and the final integrated system satisfies every CAP-1 through CAP-8 proof without default live Gemini access.

## Requirements

- CAP-8: Provide at least two factual, multi-passage, ambiguous, and unsupported cases, a repeatable local runner, deterministic all-pass checks, model-judged semantic results, machine and human outputs, and case-level manual acceptance without an aggregate threshold.
- H1: The final verification proves CAP-1 through CAP-7 behavior and all binding constraints without adding deferred feature scope. (SPEC.md, Success signal and Constraints)

## Done when

1. The diff-friendly dataset validates and contains at least two cases in every required category tied to stable fixture PDFs.
2. A controlled local runner records evidence, outcome, citations, latency, deterministic checks, semantic results, and both report forms.
3. Every deterministic check passes; semantic evidence and forbidden-claim results remain visible for case-level attended acceptance with no aggregate score.
4. A clean integrated run proves collection synchronization, supported and unsupported chat behavior, course isolation, citations, failures, and evaluation.
5. Final cleanup is limited to findings from integrated evidence, and all relevant tests, linting, and type checks pass.

## Boundaries

Evaluation artifacts, execution, cross-initiative verification, and final evidence-driven hardening. This epic verifies CAP-1 through CAP-7 but owns only CAP-8 and introduces no new product feature.

## References

- parent — _bmad-output/initiative-local-course-rag-assistant/initiative-local-course-rag-assistant.md
- spec — _bmad-output/specs/spec-local-course-rag-assistant/SPEC.md, CAP-8, Constraints, and Success signal
- evaluation contract — _bmad-output/specs/spec-local-course-rag-assistant/evaluation.md
- implementation contract — _bmad-output/specs/spec-local-course-rag-assistant/stack.md, Verification boundaries

## Notes

- Decision: Story 8.1 stops at dataset/schema, controlled runner, result records, and reports; Story 8.2 owns all deterministic and semantic checks (validated 2026-09-24).
- Decision: CAP-8 completion requires the deterministic double-backed run and attended case-level semantic review; live Gemini is optional and explicitly enabled (approved 2026-09-24).
- Decision: Story 8.3 is the single standalone integration, final refactor, and hardening story; it adds no features and fixes only findings produced by verification and evaluation (user direction, 2026-09-24).

