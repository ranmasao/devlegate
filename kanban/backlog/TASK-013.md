---
"type": "devlegate.ticket"
"title": "Harden runtime coverage using the post-0.6 audit"
---

## Milestone

Post-0.6 runtime hardening.

## Goal

Use the durable coverage analysis produced by TASK-010 to choose and implement the
highest-value runtime tests and, where justified, small decomposition/refactoring
work after the 0.6 release.

## Context

Before 0.6, runtime coverage is intentionally being audited rather than blindly
increased. TASK-010 is expected to identify clusters of missing behavioral,
recovery, defensive, and potentially obsolete logic in `runtime.py`.

The project explicitly does not want to delay 0.6 by chasing coverage percentage or
performing broad runtime refactoring. This backlog item exists so the findings are
not lost after release.

## Required behavior

- Start from the actual TASK-010 report and current post-0.6 source, not from stale
  line numbers or historical percentages.
- Prioritize tests that prove meaningful state-machine, recovery, provenance, and
  fail-closed behavior.
- Distinguish valuable behavioral coverage from low-value branch-count chasing.
- Refactor runtime code only where the audit and test work demonstrate a concrete
  maintainability or testability benefit.
- Keep decomposition incremental; do not turn this ticket into an unrelated runtime
  rewrite.
- Reassess scope before implementation and split into smaller tickets if the audit
  reveals multiple independent workstreams.

## Acceptance criteria

- The selected work is traceable to concrete findings from TASK-010 or current
  equivalent evidence.
- New tests cover meaningful behaviors rather than only raising aggregate coverage.
- Any runtime decomposition preserves existing observable workflow and recovery
  semantics.
- The final scope is small enough to review independently; otherwise the architect
  decomposes this backlog item before moving work to todo.

## Required regressions

- Existing lifecycle, recovery, execution, and control-plane regression suites remain
  green after any refactoring.
- Coverage configuration continues to measure Devlegate runtime code rather than
  vendored NanoYAML.
- No new test depends only on implementation line numbers when a behavioral
  assertion is possible.
