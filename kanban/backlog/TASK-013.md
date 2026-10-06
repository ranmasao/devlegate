---
"type": "devlegate.ticket"
"title": "Harden runtime coverage from the current decomposed test baseline"
"depends_on": ["TASK-010", "TASK-050", "TASK-053"]
---

## Milestone

Runtime coverage hardening.

## Goal

Reassess runtime coverage against the current source and the physically decomposed
test suite, then add the highest-value missing behavioral proof without chasing an
arbitrary percentage.

## Context

TASK-010 produced the original runtime coverage audit. Since then the service,
recovery, lifecycle, retry, reconciliation, logging, and test architecture have
changed substantially. TASK-049 and TASK-050 physically decomposed the large mixed
test modules into semantic domains with explicit support ownership.

Therefore the old TASK-010 line numbers and percentages are evidence of where to
look, not an implementation checklist.

Recent authoritative CI after TASK-052 reports:

```text
1135 passed, 1 skipped
aggregate coverage: 79%
Ruff: green
```

The useful target is not a preselected percentage. Coverage improvement is valuable
only when it adds meaningful proof of state-machine behavior, recovery,
provenance/authority, or fail-closed handling.

## Required behavior

- Start from the current source tree, current coverage report, TASK-010 findings, and
  the accepted semantic test layout.
- Produce a fresh gap inventory before adding tests.
- Map each high-value uncovered behavior to the narrowest semantic test domain that
  owns it.
- Prioritize missing proof around:
  - scheduler/admission and product/control synchronization;
  - durable state invariants;
  - checkpoint/publication recovery;
  - accepted integration;
  - update-base/reconciliation;
  - stranded/process-loss recovery;
  - control bootstrap;
  - polling/operator admission;
  - drop/provenance evidence;
  where those gaps still exist in current code.
- Do not recreate monolithic test modules or generic catch-all coverage tests.
- Do not add tests whose only purpose is executing lines with no behavioral
  assertion.
- Preserve stronger IPC/owner/production-topology companions where the invariant
  crosses those boundaries.
- Refactor runtime code only when current evidence shows a concrete testability or
  maintainability problem; keep any such refactor small and local.
- If the fresh gap inventory reveals multiple independent substantial workstreams,
  stop after the inventory and split follow-up tickets rather than bundling a broad
  runtime rewrite.

## Acceptance criteria

- The work is based on fresh current coverage evidence, not stale line references.
- Every added test has a clear semantic owner and states the invariant it proves.
- High-value gaps selected for implementation are materially reduced or explicitly
  split into follow-up work.
- Aggregate coverage is reported as evidence, not used as the sole success metric.
- Existing lifecycle, recovery, execution, IPC, and production-topology regressions
  remain green.
- Coverage still measures Devlegate runtime code and excludes vendored NanoYAML as
  intended.
- Ruff remains green.

## Required evidence

- Before/after coverage summary from the same current test baseline.
- Fresh gap inventory identifying the selected uncovered invariant families.
- Focused commands for each semantic domain touched.
- Full authoritative test/coverage run.
- Ruff.
