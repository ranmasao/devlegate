# Recovery Contract

Recovery has four separate decisions. Observation reads Git, worktrees, runtime
state, and worker containment without changing them. Validation proves the
immutable admission and the side effect at the current WAL boundary. Resolution
performs one lease- or CAS-guarded repair only after validation. Final
disposition is resume, complete, quarantine, or operator recovery. A failed
observation is not a final disposition when a later validation can resolve it.

## Durable Boundaries

| Phase / completed effect | Minimum proof for continuation | Resolution | Unsafe result |
| --- | --- | --- | --- |
| `idle`, `merge_pending` | Exact recorded product/control coordinates and clean owner checkout | Complete or replay the exact fast-forward | Stop before another merge |
| `agent_pending` | Ticket id, title, body, dependencies, immutable control admission, base, execution binding, registered worktree, and explicit `worker_identity: null` | Adopt only an ancestor-descendant control update with unchanged ticket meaning; reuse or repair only an exactly owned workspace | Keep evidence and require recovery; never launch a worker |
| Rework preparation before predecessor publication | Valid review binding and previous checkpoint | Repeat only the missing preparation action | Reject foreign or missing predecessor evidence |
| Rework `remote_predecessor_installed` | Registered workspace, exact published predecessor, valid review binding, explicit pre-worker marker | Preserve a dirty workspace and continue preparation | Do not clean, reset, remove, or replace dirty files |
| `worker-launch`, `worker-running` | Exact persisted worker identity and an absent containment/process observation | Normalize only after absence proof, then apply retry policy | Quarantine while live or indeterminate; no duplicate launch |
| `post-worker` | Worker is retired and no durable report exists | Normalize as lost worker outcome | Never invent a report |
| Checkpoint/publication | Bound report, exact checkpoint, clean workspace, expected remote generation | Create/recognize checkpoint then publish under lease | Preserve state for reconciliation; no ref rewrite |
| `lifecycle` | Bound report plus product and control lineage | Apply or recognize the exact lifecycle effect | Offer drop only after exact evidence and absent worker proof |
| Accepted integration | R/H/P intent and exact control/product topology | Create, publish, or recognize exact C | Reject divergent or intervening history |

Immutable admission identity includes the admitted control revision, ticket
meaning, execution id, product base, branch, and worktree path. Mutable
observations include a later control descendant and remote heads. A control
descendant is adopted only after ancestry and unchanged title, body,
dependencies, state, and runnable eligibility are proved; it never rewrites the
immutable admission revision. Unknown review fields, invalid review digests, or
invalid attribution remain rejected. The two historical inert review fields
`legacy: false` and `legacy_boundary: null` are accepted.

## Blocking And Isolation

`blocked` and `UNSAFE` diagnostics name the missing proof and preserve the
observed state. They do not authorize a mutation. A blocked lifecycle execution
can be isolated with `devlegate drop <ticket-id>` only when the exact execution,
checkpoint, report, and absent worker ownership are proven. Drop pins evidence,
records a durable disposition, retires only proven clean resources, and returns
the service to `idle`; future eligible tickets can then proceed. Pending product
reconciliation remains explicit through `devlegate reconcile <ticket-id>`.
Neither path is a force bypass.

The recovery matrix and crash-boundary test coverage are maintained in
`tests/RECOVERY_MATRIX.md`. Runtime gates are centralized in
`recovery.py`, `recovery_workspace.py`, `recovery_transactions.py`, and
`recovery_workflow.py`; their mutations repeat their immediate precondition
checks in `runtime.py`.
