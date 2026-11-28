---
"type": "devlegate.ticket"
"title": "Automatically continue requeued ticket after failed execution"
"depends_on": ["TASK-137", "TASK-138"]
---

## Milestone

Ordinary deferred-ticket continuation, backlog until explicit curator scheduling after modern retry and shared post-checkpoint/publication convergence (L2/C2) are established. Not a prerequisite for architecture simplification. Supersedes the proposed historical repair chain TASK-140–144, not its immutable execution evidence.

## Goal

A committed backlog -> todo return automatically attempts safe continuation from the exact latest currently owned failed checkpoint, retaining authentic prior review feedback when applicable and launching a fresh attempt without extra operator commands.

## Context

TASK-134 demonstrated a reviewed execution followed by a transport-failed nonempty checkpoint and todo -> backlog -> todo deferral. TASK-137/138 establish failure-gate supersession and review/deferral provenance; TASK-193 separates later scheduling; TASK-198–202 introduced common source-bound transplant and automatic transfer. Phase II found _latest_reviewed_rework_report still rejects failed-after-reviewed rather than using the latest failed H and separately attributed earlier review. The old native rebase/update-base and historical adoption proposal is obsolete. No unintegrated task branch is a product dependency.

## Required behavior

- Prove exact first-parent deferral/requeue after relevant failure, canonical runnable ticket and unchanged valid specification; supersede only the correct failure gate. Normal scheduling is the trigger; no inspect-historical, historical-admit, force/rewrite flags or new operator interface.
- Independently bind latest failed H/base/current generation and authentic prior review artifact if present. Preserve cumulative failed delta, not older reviewed code. Never relabel failure as reviewed; no invented feedback if none exists.
- Require contemporary independent custody, exact current retirement/no conflicting intent, source pin and current worktree/ref/remote/product/control proofs, cleanliness and submodule topology. Historical matching path/ref/SHA does not grant authority; dirty, foreign, missing, locked, unproved or ambiguous resource blocks with actionable reason and preservation.
- Reuse unchanged H for same base; for eligible advanced base reuse existing shared TransferTransaction and current owner admission/replay, moving the exact ordered contribution at most once with pin/lease/CAS and durable proof. Use existing eligible conflict policy; preserve H and human edits, do not auto-authorize an exceptional transfer. Do not resurrect native rebase or introduce a second transfer/receipt state machine.
- Fresh execution identity from retained or safely transferred H, authentic earlier feedback if present, normal report/review/integration; repeated restart/deferral/poll cannot launch duplicate worker or rerun settled transfer. No migration, synthetic receipts, historical adoption or TASK-134-specific path.

## Acceptance criteria

Disposable modern proved failed generation with and without prior review continues after ordinary requeue; same base no transfer, advanced eligible base one shared transfer. Latest failed delta and earlier feedback survive independently. Unproved custody or conflict refuses truthfully without destructive effect. Existing retry, reconciliation and standard worker lifecycle remain operational.

## Required regressions

1. Real Git reviewed -> failed with at least two commits -> deferral -> requeue, same base and advanced base, genuine feedback delivered and all H evidence retained; repeat without prior review.
2. Restart at pin/transfer/install/admission cuts, duplicate polling/repeated deferral, no duplicate worker or replayed effect.
3. Changed ticket/dependencies, incorrect review, dirty/foreign/recreated/missing/locked worktree, submodule mismatch, unproved generation/retirement, conflicting intent, local/remote lease/product/control races and conflict: safe refusal/preservation.
4. Normal current retry/reconciliation/review/integration remains intact. No actual historical TASK-134 resource changes.

## Operational end state and upgrade

The integrated runtime must remain usable for self-hosted development without a later ticket. Preserve unaffected workflows, immutable evidence and independent authority/effect proof. Document safe quiescent upgrade and readiness checks. No production migration, synthetic receipts or mixed-version protocol. An actual stopped local incompatible proven state may be converted once by an untracked script with exact preconditions, backup/dry-run and validation; do not fabricate authority or erase uncertain effects. Focused tests, lint, diff check and exact-checkpoint hosted CI are required for implementation.
