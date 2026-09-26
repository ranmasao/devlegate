---
"type": "devlegate.ticket"
"title": "Audit runtime coverage without modifying the product tree"
"depends_on": ["TASK-002", "TASK-003", "TASK-004", "TASK-005", "TASK-006", "TASK-007", "TASK-008", "TASK-009"]
---

## Milestone

Devlegate 0.5.5 self-hosting validation and coverage analysis.

## Goal

Perform a read-only engineering review of Devlegate's current HTML coverage report,
with special focus on `src/devlegate/runtime.py`, and return a useful diagnostic
report without modifying any tracked product file.

This task intentionally exercises a valid completed worker execution with zero
tracked implementation delta.

## Context

Current coverage is much easier to understand from coverage.py's HTML output than
from the raw `missing_branches` array in `coverage.json`. The HTML report embeds
the source, uncovered-line markers, partial-branch markers, and explanations such as
`line X didn't jump to line Y because ...`.

At the time this task was created, `runtime.py` was the dominant coverage-debt
module, with hundreds of missing branches. Earlier examples showed both fully
uncovered blocks and defensive/fail-closed partial branches.

This task depends on all currently queued 0.5.5 work so that the analysis reflects
the final code and corrected coverage scope after the other tasks complete.

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

The detailed engineering report belongs in the worker's normal final textual output
so it is preserved in the execution log. It should be structured and concise enough
to review, but may be substantially longer than `devlegate_report.summary`.

The final report should include:

1. an overview of where runtime coverage debt is concentrated;
2. the largest or most important uncovered/partial-branch clusters by
   function/subsystem;
3. a classification of those clusters by likely value/risk;
4. roughly 5-10 highest-value future test cases, ordered by engineering value;
5. any architectural/decomposition observations supported by the coverage evidence;
6. a short note confirming that no tracked product files were changed.

Use `devlegate_report` normally at the end. Its `summary` should remain a concise
semantic completion statement, not the detailed report itself; `remaining` and
`questions` retain their normal meanings.

## Zero-delta constraint

This task is intentionally analysis-only.

- Ignored development artifacts such as `.venv/`, `.coverage*`,
  `coverage.json`, and `htmlcov/` may be generated as needed.
- Before completing, verify that the tracked working tree has no changes relative to
  the execution base.
- Do not create a documentation/report file merely to manufacture a Git delta.
- Do not commit, push, or otherwise alter Git history; Devlegate owns lifecycle
  effects.

## Acceptance criteria

- Fresh branch coverage and HTML coverage are generated from the final post-task
  source state.
- The worker inspects the HTML source/branch annotations for `runtime.py` and
  produces the requested engineering report in normal worker output.
- The report identifies concrete coverage clusters and future tests rather than only
  restating the overall percentage.
- The report distinguishes valuable missing tests from low-value defensive coverage
  where possible.
- The execution finishes with zero tracked product-tree changes.
- A completed zero-delta worker claim is handled deterministically by Devlegate
  without the worker manufacturing a checkpoint change.
- The execution log remains sufficient for a reviewer to recover the detailed
  analysis after completion.
