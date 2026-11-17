---
"type": "devlegate.ticket"
"title": "Integrate scheduler decision organically into status presentation"
"depends_on": ["TASK-174"]
---

## Problem

`devlegate plan` exposes the scheduling decision, but `status` already includes the same `StatusSnapshot.plan` in its JSON/YAML payload. The ordinary human-readable `status` shows execution/workflow/repositories without explaining the scheduler's next action and reason coherently. Simply adding an isolated plan table duplicates the same project state.

## Goal

Make `status` answer together: what is happening, why, and what the scheduler currently intends to do next. Integrate `ExecutionPlan` into existing status sections and their precedence, rather than appending an unrelated second report.

## Requirements

1. Inspect `_render_status_text`, `_execution_projection`, `StatusSnapshot.plan`, live worker/lifecycle handling, blocked/failure/reconciliation, and `plan` presentation. Design one human-readable hierarchy with no contradictory repeated execution, ticket or scheduling statements.
2. Surface action (`run-worker`, `blocked`, `none`), reason, selected ticket and whether a bound execution is being resumed, as applicable; distinguish observed scheduler intention from guaranteed future action. Show why work cannot proceed when relevant. Do not claim idle when live evidence is unknown; preserve running/unverified/recovery-required semantics and lifecycle stop/restart precedence.
3. Preserve status operation with a stopped service, owner-IPC/live projection, fail-closed behavior, existing exit-status semantics, and stable machine-readable `status --json/--yaml` contracts, including embedded plan.
4. Add realistic textual tests for idle/runnable, blocked, active/bound, failed/recovery, missing control, and stopping/restarting at TTY widths 80 and 120 and non-TTY. Reviewer must inspect representative full output for readability and contradictions.
5. Scope tests by actual semantic boundary: output/status/CLI plus only relevant stronger IPC cases; use worker focused testing policy and hosted exact-checkpoint CI.

## Boundaries

Do not remove public `plan` yet (TASK-176), rewrite planner admission or `ExecutionPlan`, alter service ownership, or implement `inspect` renderers. No migration or compatibility adapter.
