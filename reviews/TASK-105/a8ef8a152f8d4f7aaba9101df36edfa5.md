<!-- devlegate-review-v1
ticket: TASK-105
execution: a8ef8a152f8d4f7aaba9101df36edfa5
checkpoint: 901a4d1a7ce526211badea62cdfd70eaa40bb217
control: a63244f43eb57ed41f7cec697678b9c9bb4cccac
disposition: rework
-->

## Iteration 1 review — REWORK for next bounded investigation

The first 239-line report is a useful, source-linked inventory and remains the cumulative baseline. It made no implementation changes, correctly distinguishes engine authority from domain helpers, and identified three bounded seams. Spot-verified runtime.py:5094-5137 vs _persist_product_reconciliation 8402-8460 as genuine duplicate logic. Also verified owner-bypass candidates (runner=_git) in checkpoint publication and drop cleanup; record them as observable technical debt, not authorization to refactor in this audit. This is an expected **research continuation**, not rejection of the first report.

### Iteration 2: investigate only cleanup's shared post-proof executor contract

Answer precisely whether the three candidate paths can share any **post-admission cleanup effect choreography** without moving owner/WAL admission or eliminating re-observation immediately before destructive steps.

1. Map actual control/data flow for zero-delta cleanup, post-accepted-integration cleanup, and generic cleanup. Trace caller, durable stage, cleanup target binding, worktree registration/topology proof, local expected-SHA check, remote lease, worktree removal, local ref deletion, partial-failure state, and re-entry/retry. Distinguish absent steps and intentional ordering differences. Cite integrated product source lines and existing focused tests.
2. Produce a 3-column (paths) comparison of **precondition / proof / effect / durable record / retry behavior**. Do not claim two sequences are equivalent solely because both call `manager.retire` or `_retire_execution_refs`.
3. Specify the smallest immutable input/output contract a reusable executor could accept, including how a changed remote/ref/registration or shutdown would cause refusal; explicitly identify which proofs must be fresh and which component retains authority for `_state`, WAL transitions, owner lock and candidate-cohort scope.
4. Decide `EXTRACT`, `LOCAL-DEDUP ONLY`, or `KEEP SEPARATE` for each common part. Identify concrete code that would disappear, any new adapter/Protocol methods, and whether a single executor is beneficial versus increased cross-module coupling.
5. Add an explicit provisional negative regression matrix for blocked ref deletion, occupied/dirty/moved/symlinked worktree, remote drift, interrupted deletion, and already-completed partial cleanup. Preserve evidence before effect and never retry an ambiguous remote deletion blindly.
6. Preserve the iteration-1 report and append a labelled Version 2 with findings and remaining questions. **Audit/docs only, no production-code edits, no general refactoring or unrelated security fixes.** Do not run full pytest merely for this report.

Scope guard: do not yet investigate the checkpoint/publication state machine or implement the easy reconciliation-persistence DRY candidate. Note direct runner=_git anomalies in risk register for future review, but do not turn this into their implementation ticket.

Acceptance remains intentionally open for further research generations. Make substantive progress and report unknowns; `completed` from a worker does not automatically close this multi-iteration audit.
