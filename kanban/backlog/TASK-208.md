---
"type": "devlegate.ticket"
"title": "Require current generation authority before checkpoint and publication"
---

## Milestone

Devlegate 0.5.8 architecture hardening after the three-phase audit. Backlog until explicitly scheduled. Independent vertical substitution; do not introduce dependencies merely because tickets share an audit theme.

## Goal

Remove unbound live checkpoint and publication authority before consolidating first-run and replay algorithms.

## Context

Phase I F2 and Phase II L1: runtime.py and recovery_workflow.py still admit legacy agent_running shapes to Git checkpoint/push before later lifecycle generation validation refuses. Historical matching remote SHA is weaker than the current bound atomic publication acknowledgement. TASK-200–202 retained independent generation and publication proofs.

## Required behavior

- Inventory all checkpoint/recovery/publish callers and guard live mutation before any Git effect (including fetch) with independently admitted matching generation, exact current retirement/result and source/predecessor proof appropriate to the stage.
- Remove unbound commit/push/recognition alternatives and no-binding receipt exceptions in this domain; no legacy live fallback, synthetic generation or inferred success from SHA equality.
- Preserve exact bound publication intent, execution ref plus acknowledgement ref, leases, local CAS, old checkpoint pins, retained same-base continuation and independently verifiable already-admitted effect settlement. Do not require new admission for settling an already proved operation.
- Retain historical settled report/receipt interpretation. Convert synthetic positive fixtures to valid current binding; retain negative insufficient-proof tests. No general checkpoint convergence or schema migration here.

## Acceptance criteria

All supported current normal/restart/same-base paths reach ordinary review or explicit blocker using one current publication authority; legacy shapes cannot perform effects. Inventory removed alternatives and tests; no unrelated drop/retry scope expansion.

## Required regressions

1. Legacy running/checkpoint/publication input lacking current generation/retirement/predecessor proof: assert no Git mutations or fetch, no receipt/worker/report fabrication.
2. Bound nonempty and no-op checkpoint, retained same-base continuation, exact publication lease and lost remote acknowledgement replay; assert correct H and no duplicated worker/effect.
3. Contradictory publication acknowledgement, remote or control/product drift and interrupted force/manual hold preserve exact source and refuse appropriately.

## Operational end state and upgrade

The integrated checkpoint must be usable for subsequent self-hosted development without any later ticket. Preserve all unaffected live workflows. Specify the affected active-state/IPC/evidence contracts, the safe quiescent installation boundary, and exact readiness checks. Preserve immutable reports, reviews, pins and necessary independent authority/effect proofs. No production migration, synthetic evidence or mixed-version protocol. If an actual stopped local installation requires conversion of proven state, describe a narrowly scoped one-shot untracked procedure with exact preconditions, backup/dry-run and validation; never invent authority or erase uncertain effects. Focused regressions, Ruff, diff check and exact-checkpoint hosted CI are mandatory.

Settle old active checkpoint/publication/lifecycle windows before installing; immutable evidence remains readable.