# Iteration phase boundary audit

## Baseline and decision

Inspected clean product SHA `8c331efb38fb40dc6a2d7eb423ec3fb1f95357f9`.
Locations refer to that revision. TASK-086's
`runtime-responsibility-audit-086.md` is a historical lead, not proof of
current behavior. The TASK-109 report and supplied `architecture.json`,
`engine_calls.dot`, `imports.dot`, and `static_report.md` are absent from this
workspace. No dynamic effect counts are inferred from static calls.

No independently closed phase with a demonstrated benefit was identified, so
no runtime extraction is made. In particular, moving a line range into a helper
would leave the same mutable owner dependencies and failure boundaries while
obscuring them. This is not final architecture approval. TASK-085 separately
owns the recovery-boundary audit; TASK-073 remains the human-only final gate.

## Phase and boundary map

The scheduler retains repository ownership and admission; `run_iteration`
clears process-local `_owned_execution_id` in a `finally` (`runtime.py:3494-3509`).
The following are conditional paths, not independent state machines. Saves are
durable application intent/stage records, not atomic transactions with Git,
worker processes, files, or remote effects.

| Predicate and location | Owner, proof and effects | Stop, failure and re-entry |
| --- | --- | --- |
| Entry, `3511-3571` | Publish processing, restore admitted forced retry; pending operator recovery validates ticket, execution and observed head. Verify branch before idle accepted-integration recovery or backlog cleanup/reconciliation. Cleanup is an effect, not a pure observation. | Stop returns except for `merge_pending`. Operator recovery and accepted/reconciliation recovery return directly; invalid binding or branch blocks. |
| `agent_running`, `3572-3616` | Checkpoint-publication stage uses existing recovery workflow; lifecycle stage recovers report or completes recorded drop; otherwise reconcile stranded execution, possibly authorizing automatic resume. | Stop prevents recovery admission. Re-entry uses persisted stage and identities, not merely an assumption that effects completed. Explicit retry cannot also authorize automatic resume. |
| Clean checkout and synchronization, `3617-3829` | Observe status, sync control, resolve product HEAD, fetch remote. Pending merge validates exact persisted local/target identities and descendant remote; pending agent may preserve a proven local-ahead head. Before product fast-forward save `merge_pending` with old and target SHAs and changed paths; verify resulting HEAD exactly and save synchronized phase. New fast-forward likewise records intent first. | Dirty, fetch, divergence or merge failure refuses progress. Stop gates precede sync and new merge admission; an already durable `merge_pending` drains despite stop. Re-entry accepts only persisted old or target local SHA and proves remote ancestry; a failed merge is not assumed complete. |
| Plan and integration, `3830-4034` | Load context/tickets, construct plan from fresh code/control observations, repair eligible bound workspace, publish projections, validate retry authorization. Accepted integration records ticket, checkpoint and control predecessor before `_integrate_accepted`; recovery revalidates checkpoint and fresh Git observations. Final idle save clears intent before cleanup/publication. | Stop gates before admission. Blocked plan and changed/missing evidence refuse. Idle entry with retained accepted intent dispatches `_recover_accepted_integration` (`4999`, `recovery_workflow.py:257-280`); crash recovery observes Git again. |
| Ticket and execution admission, `4035-4414` | Generation fingerprint, failures, retry/resume kind, bound ticket body, execution ID/base/control/remote identity and prior reviewed rework evidence determine admission. Exact previous branch head and review binding are proved before saving `agent_pending`; admitted and validated heads have different meanings. | No work, unchanged generation, unauthorized failure or stale retry returns/blocks. Stop after pending save leaves durable binding for re-entry; process-local ownership is assigned only after that gate. |
| Prepare and launch, `4416-4587` | Prepare bound worktree, restore review binding, build and persist prompt evidence. Last stop gate precedes prompt artifact and `agent_running/worker-launch` save. Worker callbacks persist identity, interruption and delivery; retired worker group is required before progress. | Workspace error blocks; closed worker admission saves idle; other worker failure retains running evidence. Re-entry reconciles stranded worker rather than launching a second owner blindly. |
| Checkpoint and product decision, `4588-4728` | Verify submodules; persist report at `checkpointing` before checkpoint commit, then save exact checkpoint in `post-checkpoint`. Observe product generation; persist zero-delta retry or reconciliation evidence, or validate safe descendant. | Integrity failure records failure; checkpoint failure leaves intent. Lifecycle drain stops after checkpoint. Recovery `recovery_workflow.py:75-167` proves binding, starting head or exact existing checkpoint, and clean workspace before advancing; unsafe product does not become ordinary publication. |
| Publication and lifecycle, `4729-4770` | Save `publishing` before leased branch publication, then `post-publication`, then `lifecycle` with report and interruption. Apply control lifecycle and finalize. | Failure can leave an ambiguous external effect. Replay `recovery_workflow.py:168-254` re-observes exact remote predecessor/checkpoint and validates report before lifecycle; it does not infer push success from WAL. Interrupted execution returns separately from worker failure. |

`_save_state` (`runtime.py:3274-3340`) and `runtime_store.py` own persisted
state, while Git operations use owner-bound runtime capabilities. Those saves
do not combine SQLite and external effects. Normal and replay paths intentionally
have different observations: for example, recovery must decide whether a
checkpoint or push already happened. Sharing only their superficially similar
lines would not establish a closed transaction or preserve those proofs by
construction.

## Focused regression evidence and gaps

On the baseline, ran:

```text
python -m pytest -q tests/test_control_recovery.py::test_checkpointing_recovery_recreates_missing_checkpoint_without_worker tests/test_control_recovery.py::test_checkpointing_recovery_recognizes_existing_exact_checkpoint tests/test_control_recovery.py::test_checkpoint_recovery_refuses_stale_product_before_side_effects tests/test_control_recovery.py::test_publishing_recovery_pushes_checkpoint_without_worker tests/test_control_recovery.py::test_service_recovers_publication_failure_without_rerunning_worker tests/test_control_recovery.py::test_post_publication_recovery_replays_lifecycle_without_worker tests/test_control_scheduler.py::test_descendant_integration_replays_interrupted_product_merge tests/test_control_git_workflow.py::test_sync_control_does_not_retry_an_interrupted_merge
```

Result: 10 passed, including parametrized cases. These cover selected missing
and existing checkpoint, stale product, publication failure/re-entry, lifecycle
replay and interrupted merge paths; they do not prove all crash timings or real
process death at every boundary. No extraction was made, so no new extraction
failure/restart test is claimed. Remaining evidence gaps include the absent
supplied artifacts, dynamic effects in indirect callbacks and Git hooks,
concurrent external writers, and process-loss coverage at every intent/effect
boundary. Any later extraction needs a closed owner/proof/result contract and
focused failure and actual restart evidence before moving these boundaries.
