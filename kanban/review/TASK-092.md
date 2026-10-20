---
"type": "devlegate.ticket"
"title": "Isolate execution-resource cleanup dependencies per ticket"
"depends_on": ["TASK-091"]
---

## Confirmed live incident (rslab2, 2026-10-20)

After TASK-091 and TASK-076 fixed accepted integration, LAB-185 successfully transitioned to `kanban/done/LAB-185.md` and its checkpoint `0687826c712464e4e35f6f79fc2fe8cc8b2ad558` is reachable from published product `origin/master` (merge `9cba7038105f1fe1498a12e7a9ae030c6e9704d2`; `merge-base --is-ancestor` exit 0). `accepted_integration` is cleared. Nevertheless, the service retains its execution worktree, local `devlegate/work/LAB-185` ref and remote worker ref; cleanup reports `runtime still has execution dependencies`.

At the same time, another worker LAB-186 is running (`worker_identity` bound to LAB-186) and a historical `reconciliation` for LAB-158 remains `resolved`. Cleanup status shows `74 candidates / 74 remaining / 74 blocked`.

The immediate cause is in `src/devlegate/runtime.py` near the cleanup branch following `_accepted_execution_report`, `_find_accepted_integration_commit`, and `_prove_published_product_checkpoint`:
```python
if self._state.get("worker_identity") is not None or any(
    self._state.get(key) is not None
    for key in ("reconciliation", "automatic_retry", "accepted_integration")
):
    raise WorkflowBlockedError("runtime still has execution dependencies")
```
This is a **global** gate: a different live worker or even an unrelated resolved reconciliation vetoes cleanup of a demonstrably completed ticket. It risks indefinitely accumulating worktrees and refs on continuously active projects.

## Scope and requirements

1. Replace the global gate with **per-execution resource ownership / dependency checks**, identifying active references using ticket ID, execution ID, branch, workspace/head, checkpoint and persisted provenance as appropriate. Unrelated LAB-186 execution and resolved LAB-158 reconciliation must not block eligible LAB-185 retirement.
2. Retain strict fail-closed protection for the **same** execution's live worker identity, pending reconciliation, retry, accepted integration, or any other durable state that can still use the candidate resources. Audit what “resolved” reconciliation means for resource liveness; do not simply ignore all resolved records without evidence. Unknown or malformed identity/ownership is not permission to delete.
3. Keep existing positive eligibility proof: ticket durably `done`, exact accepted execution report, matching registration/worktree/branch/head, validated execution refs, proven published accepted integration and product checkpoint. Do not weaken these checks or replace them with ancestry alone.
4. Before each destructive cleanup effect, revalidate ownership/state and Git observations against fresh exact identity, protecting races with scheduler/worker admission, simultaneous restart, and remote/local ref changes. No force deletion or removal of foreign worktrees.
5. Handle candidates independently: one blocked historical/ambiguous resource must not prevent retirement of a different proven-safe completed execution. Preserve useful per-ticket `workspace_cleanup.decisions` / `last_pass` diagnostics, distinguishing active own dependency from unrelated runtime activity.
6. Tests with real Git refs/worktrees and service state covering: LAB-185 proven done while LAB-186 worker live and LAB-158 resolved reconciliation persists; candidate itself actively owned (must retain); candidate-specific active reconciliation/retry/integration; missing or contradictory evidence (must retain); interrupted retirement and idempotent replay; remote ref changes during retirement (fail closed); no scheduler interruption or duplicate worker.
7. Check Arch/Debian runtime behavior remains distribution-neutral. Run focused tests and checkpoint CI; scope to cleanup semantics and related observability only, not the separate TASK-080/084/085 Git API/recovery refactors.

## Boundary

**Do not operate on the live rslab2 checkout, SQLite, worktrees, or remote refs.** Use isolated fixtures. Do not mass-delete the 74 candidates. Historical candidates with missing/ambiguous evidence remain separately blocked; this fix should retire only those for which full per-ticket proof succeeds.

QA stages and project-specific validation profiles (TASK-025–030) are deliberately outside this ticket and require separate research into project kinds and standards.
