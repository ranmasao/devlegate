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

### Additional observed interleavings and actionable defects

* `test_forced_stop_can_be_overwritten_by_inflight_owner_save` pauses an
  ordinary `_save_state` after it has constructed its full replacement payload,
  commits forced stop on another thread, then resumes the ordinary save. The
  second commit removes interruption and manual-retry fields. The test invokes
  the methods directly; it does not prove a particular production timing or
  cover a complete IPC/service process. Classification: potentially
  safety-critical loss of interruption and manual-retry authority. Independently
  actionable issue: protect forced-stop persistence against concurrent full-state
  replacement.
* `test_rewrite_observation_failure_leaves_temporary_base_in_memory` injects a
  failed workspace observation immediately after a successful rebase call. It
  observes local-pending persisted with the original base and the temporary
  target remaining in memory. This is a component simulation, not proof of
  real Git behavior or of a later unsafe action. Classification: state integrity
  defect; downstream safety impact is not established. Independently actionable issue:
  restore or explicitly reconcile temporary state on every exceptional exit.
* `test_failed_status_refresh_retains_last_published_view` commits a newer
  state and forces observation failure; the published status remains the last
  successful one. This is expected fail-closed caching, not evidence that the
  view reflects the current SQLite revision. The separate deterministic
  `test_status_and_service_publication_can_be_observed_between_generations`
  pauses between the two publication steps and observes a newer service phase
  with the preceding published status. This demonstrates a mixed read, not an
  authorization bypass. Classification: observational publication inconsistency;
  an authorization impact has not been established.

### Field-level transitions examined

Locations are in `src/devlegate/` at the baseline SHA. Every row inherits the
shallow-copy and full-row replacement behavior in the first matrix: validation
and SQLite replacement precede reference swap, but do not undo prior nested or
direct writes, external Git, or a concurrent stale replacement. A persisted
stage is evidence of an intended boundary, not proof of the following effect.

| Field family | Writers and observable transition | Failure, authority, and reentry |
| --- | --- | --- |
| `phase`, `selected_ticket_id`, `selected_ticket_body`, `selected_ticket_title`, `selected_ticket_dependencies` | `runtime.py:4807-4853` records selection and `agent_pending`; 4976-4989 writes `agent_running`; 3798-3807 clears selection on idle. | Selection is bound to control observation; descendant validation compares ticket eligibility and exact body/dependencies at 6050-6121. A failed save leaves the previous persisted selection. |
| `local_head`, `remote_head`, `control_head`, `changed_paths`, `handled_remote_head`, `handled_control_head`, `handled_todo_fingerprint` | Iteration records observations at 4807-4817, completion at 5688-5721; product adoption at 5945-5958, 6010-6047 and control adoption at 6084-6088. | These are observations and scheduling watermarks, not current Git authority. Product adoption writes intent before fast-forward and can replay after proving exact target; control adoption revalidates ancestry and ticket. |
| `execution_ticket_id`, `execution_id`, `execution_branch`, `execution_path`, `execution_base_head`, `execution_admitted_base_head`, `execution_control_head`, `execution_remote_head`, `execution_start_head` | Admission at 4807-4853; launch at 4976-4989; clear-execution at 3808-3832; rewrite resolution at `reconciliation_transactions.py:321-331`. | Durable binding is checked by invariants at 3714-3750 and is distinct from current service ownership. The original admitted base and control binding support provenance; the effective base has the exceptional temporary alias defect described above. |
| `execution_validated_product_head`, `execution_validated_control_head`, `execution_product_predecessor`, `execution_product_transition` | Admission 4826-4831, control adoption 6084-6088; product intent `admitted` then Git fast-forward then `adopted` at 6010-6047. | `runtime.py:5929-5959` recognizes exact already-applied intent on reentry; unsafe or unobservable generations block. Durable observations do not reserve the Git ref. |
| `execution_stage`, `worker_identity`, `execution_interruption_kind`, `worker_prompt_sha256`, `worker_prompt_directive`, `pending_execution_report` | Launch 4976-5007, post-worker 5018-5025, checkpoint intent/result 5074-5101, publishing/result/lifecycle 5172-5191; forced interruption 958-982. Idle clears prompt, report, interruption and identity at 3798-3807. | Prompt artifact precedes launch save; checkpoint and publication have durable intent before Git. `recovery_workflow.py:75-210` reobserves workspace, lineage, remote and report before advancing; `recovery_transactions.py:116-162` requires proof of worker absence or blocks. Stale full-row overwrite can erase forced interruption. |
| `rework_provenance` | Admission creates nested previous execution, checkpoint, control and review binding at 4797-4806; preparation stage is replaced with a shallow copy at 6163-6170. | Evidence publication and exact predecessor checks precede workspace/ref replacement at 5830-5882 and 6172-6213; replay examines preparation stage. Nested review remains shared across shallow copies. |
| `failed_executions`, `resume_required`, `force_retry_authorization`, `force_retry_provenance`, `manual_retry_required` | Forced retry saves an assembled failure, authorization, receipt and provenance at 1509-1575; completion changes failure map and clears matching manual marker at 5680-5721; forced stop writes interruption and marker at 958-982. | These are durable admission/replay records, not proof of worker absence; a stale save can discard the forced-stop marker. Explicit retry still performs admission and ownership checks at 10650-10694. |
| `automatic_retry`, `last_automatic_retry` | Zero-delta decision saves pending retry with lifecycle stage at 5133-5145; admission marks it admitted at 4842-4852; completion moves it to last record at 5695-5710; retry handling at 1953-2022. | Retry depends on observed generation and report binding; recovery rechecks zero delta and publication state at `recovery_workflow.py:91-100,143-160,240-254`. |
| `reconciliation` | Product reconciliation is persisted through `runtime.py:5156-5158` and capability `recovery_persist_reconciliation` at 10796-10802; rewrite local-pending, remote-pending and resolution at `reconciliation_transactions.py:565-578,642-650,300-331`. | Reentry examines target, cleanliness, lineage and evidence at 335-499. The object read at 337, 453 and 523 aliases current state; operation and resolved objects are shallow copies. External Git and evidence publication are not rolled back with SQLite. |
| `mutable_receipts`, `operator_recovery` | Receipt map is shallow-copied and saved under receipt lock at `runtime.py:2282-2369`; forced retry bundles receipt at 1531-1559; cleanup updates at 2532-2582. Recovery can be explicitly cleared at 3841-3842. | Receipts record admission/idempotence rather than completion of every external effect. Nested receipt values can remain shared; timeout admission and owner claim are separate events at 1234-1321. |
| `accepted_integration`, `execution_ref_retirement` | Accepted integration is refreshed at `runtime.py:8051`, and recovery clears it after integration at `recovery_workflow.py:257-278`; retirement is saved/cleared at `runtime.py:6946-7004`. | Integration and Git ref operations are external effects; replay freshly observes product/control. `recovery_save_state` at 10735-10738 additionally invokes workspace cleanup after accepted integration is cleared, so save success does not imply cleanup success. |
| `workspace_cleanup`, `workspace_cleanup_cursor`, `workspace_cleanup_event` | Direct writes at `runtime.py:2558,5289,5319,5424,6768`, some followed by save. | Memory can lead durable row after failure; cleanup evidence is observational and is not deletion authority. The carried-forward cleanup failure test exercises one such gap. |

Recovery capabilities are indirect writers, not read-only interfaces:
`runtime.py:10735-10738` forwards arbitrary keyword fields into `_save_state`,
and `recovery_state` at 10752-10753 returns the live dictionary itself.
`recovery_workflow.py:96-100,137-141,154-159,188-192,206-210,271-277`
and `recovery_transactions.py:128-129` call that forwarding capability.
The latter keeps a previously returned state alias across a save, so it can read
the old top-level dictionary after `_save_state` swaps the owner's reference.
No arbitrary mutation through an injected implementation can be ruled out by
the protocol alone.

### Directed lock and callback edges examined

* Project flock -> owner iteration and SQLite: `runtime.py:3260-3272,9414-9475`.
  The flock admits a process owner, not individual threads.
* IPC connection lock -> request admission only; dispatch occurs after release
  (`ipc_server.py:532-550`). Connection thread -> direct lifecycle callback;
  other mutations -> operator queue (`ipc_server.py:80-224`).
* Operator -> lifecycle and operator -> receipt: admission at
  `runtime.py:1234-1245`, lifecycle at 917-952, cleanup at 2532-2563.
  Admission waits on the command event only after releasing operator lock
  (1302-1321).
* Lifecycle -> supervisor and SQLite, and lifecycle -> stop callback, while
  operator is also held (917-952). Receipt -> SQLite (2287-2369). SQLite write
  transaction serializes its row update but not construction of the replacement.
* Supervisor releases its lock before worker identity, interruption and prompt
  callbacks (`worker_supervisor.py:720-758,791-792`); interruption callback ->
  SQLite (`runtime.py:5002-5004`). This path does not prove supervisor -> SQLite.
* Snapshot lock protects individual published references (`runtime.py:1043-1077,
  2709-2746,2834-2846`); service and status publications are distinct critical
  sections. The deterministic split-generation test demonstrates the gap.

These are directed source edges, not a proof of all reverse edges. Callback
implementations and injected Git/process implementations can acquire locks not
visible in these scopes. A possible inversion needs a demonstrated opposite
edge; no actual cycle or deadlock is established here.

### Additional source inventory

The literal direct writes in product source are `runtime.py:2558,5289,5319,
5424,6768` and `reconciliation_transactions.py:578,589,609,627`. The latter
first persists `rewrite_stage=local-pending`, then assigns the target only in
memory. Conflict and OSError paths restore the previous value; failed workspace
observation at 613, linearity refusal at 638-640, and exceptions in later
publication are not covered by a general restoration. The reconciliation aliases
at 337, 453, and 523 refer to the current nested dictionary; operation at 565
is a shallow copy, and `_resolve` saves a new resolved dictionary at 321-331.
Local-pending reentry checks target, workspace cleanliness and lineage before
reset and save at 451-499; remote-pending reentry reobserves remote and evidence
refs at 335-448. These checks are not proof that all exceptional paths replay.

Process-local lifecycle intent, worker drain and `_owned_execution_id` are not
SQLite fields. Durable `execution_id`, `execution_stage`, `worker_identity`,
`execution_interruption_kind` and `manual_retry_required` do not alone prove
current ownership. Receipt maps are persisted through `_save_state`, while
operator queue and locks are process-local. `execution_base_head` is durable
except for the reconciliation temporary assignment above. `workspace_cleanup`,
`workspace_cleanup_cursor` and `workspace_cleanup_event` are observations, with
direct writes that may precede persistence. The state row and separate systemd
authority table have separate transactions; Git refs and workspace are external
authorities, with their own evidence and compare-and-swap checks.

### Additional lock and dispatch evidence

`ipc_server.py:532-550` admits a connection request under its connection lock
and releases it before dispatch; `dispatch_mutation` at 80-107 invokes the
lifecycle callback directly on the connection thread, whereas retry, drop,
recovery and reconciliation use submit methods at 108-224. The owner polling
loop holds the project flock across iterations (`runtime.py:9414-9475`), claims
queued work and dispatches it on the owner thread. The flock is not a mutex
between that thread and direct lifecycle callback. Lifecycle takes operator ->
lifecycle -> supervisor (begin_drain), then SQLite and stop callback while the
first two locks remain held (`runtime.py:917-952`). Supervisor protects active
admission and removal at `worker_supervisor.py:720-723,791-792`; the worker
execution and its identity/interruption/prompt callbacks at 732-758 occur
outside that lock. This avoids asserting a supervisor -> engine lock edge on
that path. Owner publication takes snapshot lock separately from SQLite save;
snapshot and status publication are not a single generation transaction.
Operator -> receipt is demonstrated by `runtime.py:2532-2582`; receipt save
can acquire SQLite while receipt is held (`runtime.py:2287-2369`). External
callbacks and less common reentry paths remain unproven. No deadlock freedom or
actual lock inversion is established by this static survey.

`tests/test_runtime_store.py` includes a failed pre-commit rollback test and a
two-writer WAL test that holds one transaction open while a reader observes the
previous committed generation. `tests/test_service_snapshot.py` exercises a
forced-stop save failure and verifies no worker signal, unchanged durable and
in-memory state reference, and already-admitted lifecycle intent. These tests
also demonstrate cleanup inventory memory advancing on a failed save. They
cover selected interleavings only, not crash or post-commit ambiguity.

The field table is a partial source inventory, not a proof of every top-level
key or every nested writer. To finish the bounded inventory, enumerate literal
keys and keyword arguments at every `_save_state` and `recovery_save_state` call,
trace each `**fields` producer and each alias returned from `_state`, and compare
the results to the field table. Inspect indirect callbacks and injected capability
implementations separately: static enumeration cannot prove their dynamic effects.
The lock graph likewise needs all remaining owner-loop lock scopes and potential
reverse edges traced, including external callbacks. These gaps preclude a final
authority or deadlock-freedom claim. TASK-085 recovery-boundary analysis and
TASK-073 human story gate remain separate.
