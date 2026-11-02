---
"type": "devlegate.ticket"
"title": "Durable operator admission for an exact historical repair"
"depends_on": ["TASK-140"]
---

## Milestone

TASK-139 historical repair decomposition: 2/5 — authorized durable identity, before Git effects.

## Goal

Turn a successful TASK-140 inspection into a separately serialized, explicit operator-authorized `historical_repair` intent in existing SQLite runtime state. **Do not perform Git rewrite or start a successor execution.**

## Context

Use the integrated inspection outcome from TASK-140; do not assume any unintegrated TASK-139 work branch code. Current reconciliation is active-only, with owner `OperatorCommand`, mutation fingerprint, admission receipts, `_save_state`, and scheduler ownership. Do not fabricate current `execution_*` or `reconciliation.status=pending` for the failed old execution.

## Required behavior

1. Add explicitly mutable `devlegate reconcile historical-admit` (name may differ) CLI -> exact-shaped IPC -> owner command. The request must identify ticket ID, failed/reviewed execution IDs, failed checkpoint, current product target SHA, inspected control/ticket and workspace/ref identities, an explicit `--assume-historical-ownership` acknowledgment if historical continuity was not proven, and independent `--rewrite-published` authority. No bare `retry --force` fallback.
2. Under the existing single owner mutation/admission lock, **re-observe** every relevant TASK-140 proof and reject if any requested identity or current worker, workspace, submodule, ref, product, ticket, control or pending intent differs. Acknowledgment covers only missing *past* ownership proof and never waives present-time safety. Validate the ticket remains runnable in `todo` with its exact blob.
3. Persist an explicit `historical_repair` record under the existing state/validation scheme. Include request ID and fingerprint, immutable reports+review hash/path/publication, exact control/ticket identity, original base and failed checkpoint/count, current target, worktree/registration and ref observations, separately recorded operator assumption and rewrite permission, and stage `admitted`. Admission receipt and intent must be mutually consistent, durable and restart-validatable; refuse collision/mixed generation.
4. Exclude any scheduler worker generation or other mutable reconciliation for that ticket while this intent is pending; normal work for unrelated tickets remains governed by existing owner policy. Expose status as **admitted but NOT repaired**. The command must not claim repair succeeded.
5. No Git ref creation/move, remote publication, `git rebase`, worker execution or historical-report rewrite in this ticket. Replay and duplicate same-request-ID admission must not duplicate or widen authorization.

## Acceptance criteria

- One exact operator request through real CLI/IPC/owner persists the full intent and receipt and survives service restart with no code/ref/worktree changes; malformed and replay requests fail correctly.
- No execution is admitted for the pending ticket until a later ticket completes the actual repair; operator sees that work is pending, not successful.
- Active `reconcile update-base`, normal retry and unrelated tickets retain their current contracts.

## Required regressions

Disposable real-Git valid inspection -> explicit admit -> restart -> identical durable state; absent acknowledgment, wrong report/checkpoint/ref, stale preview/control/product, foreign/dirty/locked worktree, active worker, competing operator command, duplicate/colliding request-ID, interrupted pre-persist admission. Assert zero Git mutations and zero successor workers. Focused tests, Ruff and full CI.

## Boundaries

This is a durable authorization stage only. No fake BOUND record, no `phase=agent_pending` for a historical failure, no TASK-134 mutations, migration machinery, or manual editing of historical evidence.
