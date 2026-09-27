---
"type": "devlegate.ticket"
"title": "Audit runtime coverage without modifying the product tree"
"depends_on": ["TASK-002", "TASK-003", "TASK-004", "TASK-005", "TASK-006", "TASK-007", "TASK-008", "TASK-009", "TASK-011"]
---

## Milestone

Devlegate 0.5.5 self-hosting validation and coverage analysis.

## Goal

Perform a read-only engineering review of Devlegate's current HTML coverage report,
with special focus on `src/devlegate/runtime.py`, and return a useful diagnostic
report without modifying any tracked product file.

This task intentionally dogfoods the zero-product-delta review/accepted/finalization
workflow implemented by TASK-011.

## Context

Current coverage is much easier to understand from coverage.py's HTML output than
from the raw `missing_branches` array in `coverage.json`. The HTML report embeds
the source, uncovered-line markers, partial-branch markers, and explanations such as
`line X didn't jump to line Y because ...`.

At the time this task was created, `runtime.py` was the dominant coverage-debt
module, with hundreds of missing branches. Earlier examples showed both fully
uncovered blocks and defensive/fail-closed partial branches.

This task depends on all currently queued 0.5.5 work plus TASK-011 so that the
analysis reflects the final code, corrected coverage scope, and the workflow that
can durably review and finalize an analysis-only result.

## Required work

- Do not edit, add, delete, or stage any tracked repository file.
- Generate a fresh coverage result for the execution workspace using the current
  source. Set up the development environment if necessary.
- Generate HTML coverage output with coverage.py after the coverage run.
- Inspect the generated HTML for `src/devlegate/runtime.py`; do not rely only on
  percentages or the raw `missing_branches` JSON array.
- Group coverage holes by function and subsystem rather than listing hundreds of
  line-number pairs.
- Distinguish, where evidence permits:
  - high-value missing behavioral/recovery tests;
  - defensive or fail-closed paths that are legitimately rare;
  - large completely unexecuted blocks;
  - suspiciously unreachable, obsolete, or over-complex paths that deserve
    architectural inspection rather than blind coverage chasing.
- Identify the most valuable concrete candidate tests to add later, with function or
  source-region references and a brief rationale.
- Note whether the concentration of uncovered logic suggests useful future
  decomposition boundaries inside `runtime.py`.
- Do not make those test/refactor changes in this task.

## Reporting

Return the detailed engineering analysis as the optional long-form typed worker
report introduced by TASK-011 so Devlegate persists it as durable control-plane
execution evidence.

The report should include:

1. an overview of where runtime coverage debt is concentrated;
2. the largest or most important uncovered/partial-branch clusters by
   function/subsystem;
3. a classification of those clusters by likely value/risk;
4. roughly 5-10 highest-value future test cases, ordered by engineering value;
5. any architectural/decomposition observations supported by the coverage evidence;
6. a short note confirming that no tracked product files were changed.

Use `devlegate_report` normally at the end. Its `summary` remains a concise
semantic completion statement; the detailed analysis belongs in the long-form
report payload. `remaining` and `questions` retain their normal meanings.

The runtime execution log may contain a rendered copy, but the durable control-plane
report is the review artifact and must not depend on retention of the operational
log.

## Zero-delta constraint

This task is intentionally analysis-only.

- Ignored development artifacts such as `.venv/`, `.coverage*`,
  `coverage.json`, and `htmlcov/` may be generated as needed.
- Before completing, verify that the tracked working tree has no changes relative to
  the execution base.
- Do not create a product documentation/report file merely to manufacture a Git
  delta.
- Do not commit, push, or otherwise alter Git history; Devlegate owns lifecycle
  effects.

## Expected workflow

A valid completed worker handoff with the durable report and zero tracked product
delta must follow:

```text
todo -> review -> accepted -> done
```

The reviewer/architect accepts the report if it satisfies this ticket. Devlegate
then performs a proven no-op finalization under TASK-011: product history remains
unchanged, execution workspace/branch state is retired safely, and durable
control-plane evidence is preserved.

## Acceptance criteria

- Fresh branch coverage and HTML coverage are generated from the final post-task
  source state.
- The worker inspects the HTML source/branch annotations for `runtime.py` and
  produces the requested engineering report as durable long-form execution
  evidence.
- The report identifies concrete coverage clusters and future tests rather than only
  restating the overall percentage.
- The report distinguishes valuable missing tests from low-value defensive coverage
  where possible.
- The execution finishes with zero tracked product-tree changes.
- No product commit is manufactured for the analysis-only result.
- The ticket reaches `review` through the ordinary valid worker handoff.
- After architectural acceptance, the ticket can pass through `accepted` and be
  finalized to `done` without changing the product branch.
- The long-form report remains recoverable from control history after execution
  workspace/branch retirement.
