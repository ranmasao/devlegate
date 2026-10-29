# State and authority audit (working inventory)

Baseline: product commit `9b49bed8bc27f3b2d9c2c339e684d849a87e3d61`,
clean before this work. Locations below refer to that commit. The earlier
`runtime-responsibility-audit-086.md` is a lead, not evidence for this SHA.
The supplied architecture graphs and TASK-109 report were not present in the
workspace. This is a working inventory, not final architecture approval.

## Transition and authority matrix

| Transition or authority | Evidence and effect | Boundary and failure behavior |
| --- | --- | --- |
| State save | `runtime.py:3782-3848` copies the top-level dict, applies phase clearing, validates, replaces the SQLite row, then swaps the in-memory reference. | Validation or replace failure prevents the reference swap. This is a shallow copy: existing nested values and caller-provided objects can remain shared. Earlier direct mutations are not rolled back. |
| SQLite payload | `runtime_store.py:48-71,193-226`: WAL, FULL synchronous, `BEGIN IMMEDIATE`, read revision, insert/update, commit; rollback on exception. | A revision is an observation token, not a compare-and-swap precondition. A later writer can replace an earlier writer's full payload. Git, processes and control files are not part of this transaction. JSON serialization occurs before its exception handler. |
| Execution admission and stages | `runtime.py:3937-4051` handles pending recovery before fresh scheduling; `_save_state` and worker callbacks record execution identity and stages. | Persistent stage is intent/evidence for replay, not proof that an external effect completed. `live_execution_evidence` at 2663-2694 additionally requires current-service ownership and, for a running worker, matching live identity. |
| Forced lifecycle | `runtime.py:911-982` nests operator then lifecycle lock, starts drain and records forced interruption before signalling. | If save fails, the signal is not sent, but intent/drain have already changed; test `test_forced_stop_save_failure_does_not_signal_worker` records this distinction. |
| Reconciliation rewrite | `reconciliation_transactions.py:560-650` saves local-pending intent, temporarily changes in-memory base, performs rebase, proves rewritten lineage and publishes evidence before resolving. | Some error paths restore the base explicitly; others do not have a general `finally`. Remote and local Git effects require independent proofs and are not rolled back by SQLite. |
| Cleanup observations | `runtime.py:2558-2582,5289-5305,5319-5325,5423-5430,6765-6768` directly assign cleanup inventory, cursor or event. | Some assignments precede a save; a failed save can leave memory newer than SQLite. The per-ticket event is assigned without an immediate save at that location. Cleanup views are not authorization to delete refs. |
| Systemd authority | `runtime_store.py:228-285` stores it in a separate table and separate transaction. | It is distinct from the runtime payload and its revision. |

## Publication and locking observations

The nonblocking project flock at `runtime.py:3260-3272` is single-owner
admission, not a lock around each in-process state access. Hosted repository
access checks the owner thread at 1023-1040. The process-local operator and
lifecycle RLocks, receipt Lock and snapshot Lock are initialized at 836-866.
Observed nesting includes operator -> lifecycle in `request_lifecycle`, and
operator -> receipt at 2532-2563. Receipt handling copies its top-level map
before saving at 2282-2369; `_mutable_receipt` returns only a shallow copy at
2217-2280. The lifecycle request may execute a stop-event callback while both
operator and lifecycle locks are held (925-952). SQLite acquires its own write
lock on save; it does not serialize the entire engine-level read/modify/save.

The snapshot lock protects published references, not canonical state. Service
snapshot replacement and status/live/candidate publication use separate lock
acquisitions (`runtime.py:2696-2746,2823-2846`), so separate calls are not a
single atomic multi-view read. Published status and retry views use cached
values; drop candidates instead derive from current `_state` under the snapshot
lock (1048-1077). Refresh failure keeps the prior status (2848-2854); freshness
is therefore last successful publication, not guaranteed current remote or
SQLite state. Status observation and revision checks cannot atomically bind
external Git observations to SQLite. Destructive actions still require their
own fresh ownership, evidence and CAS/lease checks.

## Regression evidence and open work

`tests/test_runtime_store.py` includes a failed pre-commit rollback test and a
two-writer WAL test that holds one transaction open while a reader observes the
previous committed generation. `tests/test_service_snapshot.py` exercises a
forced-stop save failure and verifies no worker signal, unchanged durable and
in-memory state reference, and already-admitted lifecycle intent. These tests
also demonstrate cleanup inventory memory advancing on a failed save. They
cover selected interleavings only, not crash or post-commit ambiguity.

The nested alias and indirect callback inventory is not yet exhaustive. A
complete lock-order audit must trace IPC dispatch, worker supervisor callbacks,
all owner-loop lock scopes, and reverse edges, including calls into external
code. Further deterministic tests are needed for forced-stop versus owner
save, publication freshness interleavings, and
reconciliation alias failure paths. These gaps preclude a final authority or
deadlock-freedom claim. TASK-085 recovery-boundary analysis and TASK-073 human
story gate remain separate.
