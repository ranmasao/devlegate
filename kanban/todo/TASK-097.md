---
"type": "devlegate.ticket"
"title": "Reconcile runtime validation, admission, blocking and crash recovery across lifecycle"
---

## Problem

An interrupted self-hosted Devlegate execution can become indefinitely blocked although its surviving state admits a safe continuation. Local checks may be individually defensible but collectively contradictory: an early planning refusal can prevent later validated adoption, or a restart may reapply a pre-transition requirement after that transition was durably completed. Fixing individual error messages or adding a generic force switch would leave this class of failure intact.

TASK-088 (2026-10-22/23) exposed several distinct failure surfaces:

- Following WSL process loss, rework had already reached persisted `rework_provenance.preparation_stage=remote_predecessor_installed`, with `execution_remote_head=931266f0ef363864bd20f2bd3b4487ba03ec9074`. Local/remote branch and registered worktree HEAD agreed, yet preparation unconditionally required the **previous** checkpoint `efb852661aa29c8aacade1cefc240c471312c38a`.
- The interrupted worker left uncommitted changes in `tests/test_control_recovery.py` and `tests/test_control_workspace.py`. These represent recoverable work, not disposable workspace dirt.
- Existing persisted `rework_provenance.review` included `legacy=false` and `legacy_boundary=null`, while `restore_review_binding` rejected any keys beyond its five-key current schema. A changed serialization contract blocked startup before recovery could be considered.
- While the service was being repaired, new backlog tickets advanced `devlegate/control` from the execution's admitted `c7df12ef...` to `d4671e03...`, without changing the bound TASK-088 body. In `agent_pending`, planning immediately refused a changed control HEAD, ahead of `_adopt_validated_control_descendant`, whose purpose is to prove safe descendant adoption.
- The service could become ready while its only bound execution remained `recovery-required` / `plan.action=blocked`, preventing further self-hosted progress. Diagnose the separate failed-execution retryability messages without conflating them with the actual planning gate.

These are observations from one incident, **not** proof that every case should automatically resume. Treat manually applied emergency hotfixes as diagnostic evidence, not an architectural design to preserve.

## Investigation and design

1. Audit all decisions leading to `blocked`, `UNSAFE`, admission refusal, workspace recovery refusal, and interrupted-execution retry refusal across planning, runtime, provenance, Git operations, workspace ownership, worker supervision, and control lifecycle. Trace their ordering and identify overlapping or contradictory judgments; map each to the transition and persisted evidence it actually protects.
2. Explicitly distinguish **observation**, **proof/validation**, **resolution** (including safe adoption or repair), and **final disposition**. Avoid a premature terminal refusal for a discrepancy with an available, validated reconciliation path. Equally, never proceed past a boundary whose minimum safety proofs remain unavailable.
3. Define a coherent transition/re-entry contract for admitted, pending, workspace-prepared, worker-running, post-worker, and lifecycle phases, including rework-specific stages. Ensure each durable state has a defined safe continuation, completion, quarantine, or operator-recovery outcome. Persistence must be sufficient to recognize already-completed side effects; do not replay a destructive step merely because control returned to its enclosing function.
4. Separate immutable admission identity from mutable observations, including control descendants and remote predecessor publication. A benign change to unrelated backlog content must not invalidate an otherwise unchanged bound ticket when ancestry, eligibility, body/prerequisites and exact execution identity can be proved. Do not silently adopt divergent control history, changed work, altered prerequisites or foreign ref updates.
5. Reconsider dirty-worktree classification in the context of interrupted worker work. Preservation and safe ownership matter more than a blanket clean/dirty test. Prove expected branch/HEAD, registration, worker absence, provenance and permitted continuation before reusing uncommitted files; do not confuse 'no worker identity persisted' with universal proof that no worker exists.
6. Audit persisted schema/serialization compatibility at restart (including review provenance) and define intentional compatibility/migration rules, explicit rejection of truly unsupported cases, and tests using previously written state. Do not weaken identity/content/hash checks or retroactively trust arbitrary unknown metadata.
7. Define how a self-hosted service exposes recoverable execution failures without globally deadlocking its maintenance backlog. Explore safe isolation/quarantine and operator-facing high-level recovery actions rather than requiring users to choose internal validator exceptions. A blanket 'skip most checks' force path is **not** the desired solution; assess whether any narrowly scoped operator override remains necessary after normal recovery is repaired.
8. Design diagnostics that identify the unmet proof, observed and expected identities, already-proved facts, and non-destructive next action. Distinguish actual safety conflicts, incomplete observation, compatible evolution, and implementation ordering faults.

## Implementation constraints

- Preserve fail-closed safeguards for competing/live workers, unknown execution ownership, unauthorized remote changes, Git CAS/lease races, corrupt immutable evidence and changed ticket meaning.
- Never silently reset, clean, detach, replace, re-cherry-pick over or force-push a dirty worktree. Preserve TASK-088's published commits, evidence and uncommitted modifications. Avoid manual SQLite/refs editing as the routine recovery path.
- Do not merely move or disable checks to make the observed scenario pass. Centralize or make transition-aware validations where it reduces contradictory logic, while retaining explicit precondition checks immediately before mutations.
- Keep the work reviewable in coherent stages if needed (audit/invariant map, transition contracts, implementation, regression tests). Do not let a large redesign unnecessarily suspend immediate maintenance fixes.

## Verification

- Real-Git and restart/crash-injection tests at evidence pinning, branch transplant, remote predecessor publication, pending admission, worker handoff, dirty-worktree continuation and lifecycle boundaries; verify repeated recovery remains idempotent.
- Reproduce TASK-088's exact persisted phase/stage/HEAD/review-schema/control-descendant progression with preserved dirty files. Exercise unchanged and changed ticket/control content, normal and foreign remote updates, compatible and incompatible persisted schemas, missing/uncertain and active worker ownership, and stale/replayed transitions.
- Assert both safe progress and forbidden side effects: no unexpected writes/ref moves, lost worker edits, duplicate worker launches, broken evidence or acceptance of competing ownership. Test genuine conflicts remain blocked.
- Prefer behavioral and safety invariants over incidental exact Git call counts/complete read-only call traces; coordinate with TASK-098 instead of duplicating its suite-wide audit.
- Run focused tests and full CI on committed code. Document the final validation/transition model and the diagnostic/operator recovery contract.

## Acceptance

1. Every inspected durable execution state has an explicit recover/resume/complete/quarantine path, with clearly defined proof requirements; no preemptive gate masks a later legitimate resolution.
2. TASK-088's multi-step crash-recovery reproducer resumes safely when facts are compatible and stays blocked without damage when identities or ownership conflict.
3. Benign control descendants and persisted-format evolution no longer create avoidable permanent service blockages; relevant provenance protections remain intact.
4. Self-hosted operations have an intelligible recovery or isolation path that does not demand expertise in internal validator names.
5. Tests cover transitions, restart idempotency and rejected unsafe paths; design rationale is recorded.

Priority: **high**. This ticket **supersedes the former narrow TASK-097 scope**; it is the systemic follow-up to TASK-088 and emergency recovery. TASK-098 remains responsible for the separate suite-wide brittle-test audit.
