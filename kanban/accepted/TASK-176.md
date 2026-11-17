---
"type": "devlegate.ticket"
"title": "Remove redundant public plan CLI and IPC while retaining scheduler model"
"depends_on": ["TASK-175"]
---

## Context

The `plan` command has no independent planning algorithm: the running service replies with `published_status_snapshot.plan`, and stopped-service `plan_view()` recollects the status observation. After TASK-175, ordinary `status` is the complete operator-facing scheduling view.

## Goal

Remove redundant public `devlegate plan` and its read-only IPC method, with no loss of status capability.

## Requirements

1. Remove `plan` from CLI parsing/help and dispatcher, `_render_plan_text`, unnecessary public CLI wrapper and the dedicated IPC read-only endpoint. Eliminate dead read-only plan-only routing/published accessor where no remaining legitimate users exist.
2. Keep `ExecutionPlan`, `_make_execution_plan`, the real scheduler's plan decisions, `StatusSnapshot.plan`, and any decoding needed for `status`. Do not remove a private function merely because its name contains "plan"; trace actual consumers first.
3. Keep `status --json/--yaml` with the complete planning fields and preserve status behavior while service is running/stopped. Reject removed CLI/IPC `plan` explicitly; no compatibility alias.
4. Update tests for CLI parser, status IPC dispatch/transport/client, owner/cross-layer/topology as actually relevant, plus current docs and concurrency descriptions. Replace plan-only safety tests with corresponding status tests rather than deleting proof.
5. Verify actual public contract removal and focused regressions; Ruff and hosted exact-SHA CI required.

## Boundaries

No scheduler redesign, Git/runtime state migration, implicit service startup, or semantic modification to `inspect service`.
