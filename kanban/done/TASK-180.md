---
"type": "devlegate.ticket"
"title": "Render inspect workspace without weakening reset identity proof"
"depends_on": ["TASK-179"]
---

## Goal

Replace generic single-cell JSON dump for `inspect workspace` with readable inventory of the selected ticket's independent workspace, registrations, Git observations, resource generations, proof/dirty/locks and blockers.

## Requirements

1. Provide semantic overview of exact ticket identity, control binding, workspace paths/refs/heads and local/upstream state, worker/resource generations and safety proofs, grouped by purpose with explicit unknown/unproven statuses and prominently visible blockers.
2. Treat vast artifact trees/detailed identity as machine-readable evidence; human output must summarize accurately, without dropping safety-critical conclusions or suggesting an unsafe reset/prune. No unbounded horizontal JSON lines; never misrepresent long tokens or Unicode.
3. Preserve the **exact** existing `devlegate.inspect-workspace.v2` identity, canonical digest, snapshot_complete, blockers and reset authorization/replay contract; presentation is not mutation permission. No changes to `inspect_clean` semantics.
4. Validate representative proven and unproven generations, dirty/locked/submodule and disconnected cases, large nested data, 80/120 TTY and non-TTY outputs. Independent reviewer examines rendered fixtures and checks read-only/digest regressions.

## Boundaries

Presentation only. No reset, cleanup, Git effect, resource-generation proof or schema changes. Focused tests, Ruff and exact-checkpoint hosted CI.
