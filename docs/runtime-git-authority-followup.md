# Runtime Git authority audit

This is the final source audit for TASK-108. It was made against the current
`src/devlegate/runtime.py` by enumerating direct `_git(` calls, `runner=_git`,
Git helper calls with no runner, and callback/default-runner definitions, then
tracing their callers into `src/devlegate/git.py`. Tier 1 is a local read,
Tier 2 is a remote read, and Tier 3 changes local or remote Git state. A fetch
is Tier 3 because it writes objects and tracking refs.

## Candidate register

| Candidate | Actual command and tier | Owner/stop contract | Disposition |
| --- | --- | --- | --- |
| `_git_runtime` (2906) | Direct `_git`; arbitrary command supplied by typed helpers | Calls `_assert_repository_owner()` immediately before every spawn. A matching signal failure becomes `ShutdownInterrupted`; it does not retry. | Required runtime transport and the only workflow transport. |
| `_remote_exists_for_status` (3015) | `git remote`; Tier 1 | No owner or stop gate. Its only caller is `check_result()` for the displayed `configured remote` diagnostic. `GitObservationError` becomes `False`. It cannot admit work, update state, or select a mutation. | Intentional standalone presentation exception. No fetch or mutation is possible through `remote_exists`. |
| `_is_same_parent_replacement` (734) | `cat-file -e`, `rev-list --parents -n 1` through `commit_parents`; Tier 1 for each input | It now requires an explicit runner. Its only caller, `reconciliation_same_parent_replacement`, supplies `reconciliation_git`. | No default runner remains. |
| `reconciliation_git` (10634) | Callback transport for reconciliation helpers | Refuses a pending stop before invoking `_git_runtime`; `_git_runtime` also checks the owner for every nested command. | Required fresh-admission capability. |
| `retirement_git` (6816) | Callback transport for `delete_branch_if_unoccupied`: worktree observations, exact ref observation, compare-and-delete, and possible create-only restoration | Checks owner. It refuses stop before the delete, but permits the helper's required post-delete proof/restoration after a successful delete. | Required admitted cleanup drain capability. |
| `_observe_product_generation` optional callback (8272) | Product checkout observations; Tier 1 | Default is `_git_runtime`; the reconciliation caller explicitly supplies `reconciliation_git`. | No raw/default subprocess path. |

Every remaining `runtime.py` Git helper invocation explicitly supplies
`runner=self._git_runtime`, uses a named wrapper that does so, or occurs within
one of the two callbacks above. The exception constructor near the bottom of
the module is not a helper invocation. There are no other direct `_git` calls,
`runner=_git` expressions, or reachable default-runner helper calls.

## Workflow ledger

The authority migration changes the runner capability, not the commands,
their order, leases, or CAS arguments. The before and after command sequences
below are therefore identical except for the owner-aware runner.

| Workflow | Executed commands/effects | Before/after and replay boundary |
| --- | --- | --- |
| Checkpoint publication and product reconciliation | Exact ref probes (`show-ref`, `rev-parse`), create-only `update-ref`, remote `ls-remote`, leased `push` | Evidence identity is exact and the pin precedes reconciliation state. Shutdown before reconciliation transport prevents every command. An interrupted pin or push leaves prior WAL state and re-entry re-observes the exact ref; it never blindly retries publication. |
| Accepted integration | Ancestry and parent observations, leased product/control pushes, product/control fetch, fast-forward merge, report reads | Product and control identities are proved before advancing their WAL stages. A push/merge interrupted after an ambiguous outcome is re-observed by exact ref or checkout proof before any continuation. Admitted work drains without a new stop gate. |
| Lifecycle publication | Control fetch, exact ref/ancestry observations, add, commit, reset/fast-forward, leased push | Local commit/reset and remote publication retain the existing durable lifecycle stages. Re-entry proves the remote predecessor and exact intended control commit before continuing. |
| Rework and operator recovery | Exact local/remote evidence probes, create-only evidence pins, leased evidence/branch pushes, worktree removal, expected-old ref CAS | Local and remote evidence use exact identity and empty/predecessor leases. Interruption after publication, removal, or CAS leaves evidence and state for re-observation; recovery does not overwrite a divergent ref. |
| Drop and execution cleanup | Disposition reads/writes, evidence pin, remote lease delete, worktree removal, local compare-and-delete | Disposition/evidence precede destructive cleanup. `execution_ref_retirement` is saved before local delete, permits only exact create-if-absent repair after a crash, and clears only after the helper transaction is proved complete. Remote deletion is leased and re-observed. |
| Status preflight | `git remote`; Tier 1 | The sole independent exception above. It is fail-closed presentation only and has no workflow transaction. |

Helper expansion is material to the ledger: exact ref resolution may run quiet
`show-ref`, identity `show-ref`, and `rev-parse`; `delete_branch_if_unoccupied`

## Interruption evidence

Focused real-Git regressions establish the changed boundaries:

| Proof | Test location |
| --- | --- |
| Owner denial and pending shutdown before an execution-ref delete invoke no Git | `tests/test_control_workspace.py::test_retirement_denial_does_not_spawn_git` |
| A non-owner reconciliation mutation is rejected before its runner can invoke Git | `tests/test_control_workspace.py::test_reconciliation_git_rejects_nonowner_mutation_before_git` |
| Shutdown after local delete completes occupancy restoration rather than stranding an attached ref | `tests/test_control_workspace.py::test_retirement_restores_occupied_ref_when_shutdown_follows_delete` |
| A crash after local deletion restores only the persisted exact ref on restart | `tests/test_control_workspace.py::test_retirement_restart_restores_occupied_ref_after_delete_crash` |
| Restart converges after worktree, local-ref, and remote-ref retirement boundaries | `tests/test_control_workspace.py::test_workspace_cleanup_restart_converges_after_each_retirement_boundary` |
| Reconciliation refuses shutdown before its runner can mutate | `tests/test_control_workspace.py::test_reconciliation_git_rejects_shutdown_before_mutation` |
| A successful but interrupted drop-delete push is re-observed and completed on restart | `tests/test_daemon_lifecycle.py::test_restart_after_drop_disposition_finishes_drop_without_lifecycle` |

These tests also preserve the distinction used by all runtime workflows:
shutdown before a fresh command is an admission denial, while shutdown during
or after an irreversible command leaves durable WAL/evidence and requires exact
re-observation on safe re-entry. The transport never performs a generic retry.
