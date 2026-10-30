# Git boundary audit

## Baseline and limits

Audited source at `ee6c479b3e8cf4a29168d5b1e34c4b6a7e1f2b6e`, with a clean
working tree before this document. Locations below refer to that SHA. This is
an audit, not an implementation plan approval. TASK-085 owns the separate
recovery-boundary audit and TASK-073 remains the human-only final story gate.
No compatibility migration or generic VCS framework is proposed.

`runtime-responsibility-audit-086.md` and `git-effect-trace-113.md` are leads,
not evidence that their historic line numbers or counts describe this SHA. The
TASK-109 report and supplied `architecture.json`, `engine_calls.dot`,
`imports.dot`, and `static_report.md` were not present in the workspace. The
source, tests and recorded Git history are the evidence used here. In
particular, the TASK-086 evidence-pin race recommendation is stale: current
`runtime.py:8785-8802` uses create-if-absent and re-observes the exact pin.

## Ownership and semantics

| Boundary | Admission and effect owner | Contract and important distinction |
| --- | --- | --- |
| `git.py:31-49,52-217` | Caller owns policy; runner invokes Git | `run_git` is subprocess transport. Thin effect functions construct commands and return raw results; a nonzero write can be ambiguous, so the transaction must re-observe. Fetch is a local tracking-ref write as well as a remote read. `update_ref` is unconditional; `create_ref_if_absent` and `compare_and_swap_ref` are materially different operations. |
| `runtime.py:2947-2960,3994,6407,6957,8748` | `ServiceEngine` retains repository ownership, admission, durable state and lifecycle sequencing | `_git_runtime` asserts owner and applies shutdown policy before check semantics. Publication and retirement require exact heads and leases. Persisted intent is not proof that a Git effect completed. |
| `control_git_workflow.py:44-115,118,227` | Engine supplies owner-bound capabilities; workflow sequences checkout operations | Sync validates cleanliness, fetches, checks ancestry and can fast-forward. The injected merge can preserve owner-specific interruption/WAL handling; replacing it with the default is not equivalent. Attach/bootstrap have different preconditions. |
| `execution_workspace.py:94-158,158-325,383-435,584-620` | Manager performs effects using its runner after owner admission | Creation, rework, checkpoint, submodule and retirement have distinct identity and worktree checks. Rework uses CAS and cherry-pick replay; checkpoint stages and commits. Its direct runner calls include `check=True` defaults, unlike many Git effect wrappers. |
| `recovery_workflow.py:27-72,75-210` | Replay workflow sequences owner capabilities; engine owns authority and state saves | It proves report binding and checkpoint exactness, then compares the fresh remote to the admitted predecessor or checkpoint before push and stage advance. It cannot infer success from WAL stage alone. |
| `recovery_transactions.py:44-99`; `recovery_workspace.py:190-311` | Recovery transaction under owner admission/lock; workspace manager for preparation | Control and workspace recovery pin displaced evidence create-only, re-observe and revalidate before reset/CAS. These paths use imported `run_git` directly, not `_git_runtime`; admission/locking exists but foreground shutdown policy is not automatically inherited. Assess each caller before changing runner. |
| `reconciliation.py`; `reconciliation_transactions.py:90-158,161-218,321-380,581-698` | Pure lineage proof in the former; transaction sequences owner state and Git in the latter | Admission binds ticket, target, evidence and workspace; rewrite evidence precedes leased publication. Transaction accesses `owner._state` and `_save_state` directly as well as declared capabilities, so this is not an independent state owner. Preserve rebase abort, reset and replay decisions. |

`git.py:325-414,903-930` distinguishes a proven absent exact ref from an
unreadable ref, malformed output, changed identity, or an arbitrary failed
revision. Exact commit refs are checked using presence, structured ref identity
and commit resolution; the values must agree. `is_ancestor` (`439-473`) maps
only status 1 to a negative answer; other errors remain errors. `path_object`
(`933-959`) uses successful empty structured output for absence. `read_object`
(`989-1013`) proves exact object absence first. `remote_ref` (`714-733`) uses
empty successful output for absence and checks the object ID but does not
compare the returned ref field with the requested ref: this is a concrete
malformed/mismatched-response gap worth a targeted repair investigation,
especially because its results feed lease and cleanup decisions. Do not assume
that any nonzero Git exit is absence. `resolve_object` (`417-436`) and some
observers also have different race and exception normalization behavior; do not
merge them merely because they resolve an object.

`delete_branch_if_unoccupied` (`git.py:220-312`) combines occupancy checks,
expected-old deletion and create-only restoration after a detected race. Git
does not atomically combine occupancy and ref CAS; its documented foreign
worktree race limit remains. This is a transaction, not a one-command wrapper.

## Indirection and duplicate construction

* Thin command wrappers include `fetch_ref`, `push_refs`, `merge_fast_forward`,
  `add_all`, `commit`, `add_worktree`, and `reset_hard` in `git.py:101-196,315`.
  They standardize argv but do not supply admission or postconditions. Deleting
  them would expose command construction; adding another facade would add
  indirection without establishing authority.
* `execution_workspace.py:250-256` and `recovery_workspace.py:301-305` build
  `update-ref` with an old SHA directly, alongside the equivalent primitive in
  `git.py:67-87`. `execution_workspace.py:418` and
  `recovery_workspace.py:290-294` construct worktree removal directly, also
  available in `git.py:181-196`. These are candidates for small command-level
  merges only after checking argv, `check`, runner, errors and interruption
  equivalence; the surrounding proofs must stay local to their transaction.
  `execution_workspace.py:598-620` uses `add -A` and a conditional commit;
  blindly replacing it with a raw-result wrapper would change failure handling.
* Control sync has eight callable inputs including optional merge
  (`control_git_workflow.py:50-57`); recovery declares 26 methods in
  `WorkflowRecoveryCapabilities` (`recovery_workflow.py:27-72`). These are
  static interface counts, not invocation or Git-effect counts. Runtime
  adapters at `runtime.py:10796-10938` include many one-line forwards, but some
  encode real authority or policy. The three protocol methods missing in the
  older TASK-086 report are now declared at `recovery_workflow.py:30,48-56`.
  Reconciliation's protocol (`reconciliation_transactions.py:54-87`) covers
  only part of its actual owner dependencies, including private state accesses.
  A wider facade would make the dependency less visible, not eliminate it.

## Effect evidence and regression proof

The following are historical measured top-level runner calls from
`git-effect-trace-113.md` at `baeee877466a84522d0b2331ba16d0bb7ec0d2fc` plus
that report's audit test changes. Local/remote categories overlap, e.g. fetch is
both remote read and local write. They are scenario counts, not per-method
static call counts or a bound on nested Git, hooks, network and server effects.
Current source differs in `runtime.py` and `reconciliation_transactions.py`
from that SHA; do not extrapolate these numbers to all current executions.

| Scenario | Runner calls | Local read | Remote read | Local write | Remote write |
| --- | ---: | ---: | ---: | ---: | ---: |
| Control sync current / fast-forward | 14 / 17 | 13 / 15 | 1 / 1 | 1 / 2 | 0 / 0 |
| Evidence absent / already matching | 5 / 6 | 4 / 6 | 0 / 0 | 1 / 0 | 0 / 0 |
| Publication, absent remote | 3 | 1 | 1 | 0 | 1 |
| Publishing recovery iteration | 97 | 83 | 10 | 6 | 2 |
| Eligible cleanup screening / apply | 26 / 104 | 22 / 82 | 4 / 19 | 1 / 7 | 0 / 1 |
| Cleanup screening: zero / 32 ref-only skipped | 4 / 100 | 3 / 99 | 1 / 1 | 0 / 0 | 0 / 0 |

On the audited SHA, ran `python -m pytest -q tests/test_git.py` plus the 11
explicit trace, evidence race/restart, publication lease and cleanup
restart/ownership cases listed in `git-effect-trace-113.md:83-88`: 63 passed,
one skipped. The skipped Git test requires C.UTF-8, unavailable here. These
tests exercise selected real Git paths, not every interruption point or foreign
writer. Trace tests rerun their assertions; the historical table is retained as
historical rather than claimed as a fresh full measurement. In particular the
recovery case simulates a crash and resumes an iteration; it is not proof of an
actual killed-and-restarted service process. Test setup and verification calls
are excluded from historical counts.

## Ranked recommendations

1. **Repair: exact remote identity validation.** Investigate and test
   `remote_ref`'s missing returned-ref equality check, including malformed,
   unexpected single-ref and multi-ref responses, exact absence, normal remote
   and lease/replay paths. Preserve existing error vs absence distinctions and
   verify caller inputs before changing behavior. This is a targeted candidate,
   not a demonstrated production exploit.
2. **Keep: transaction ownership and proof boundaries.** Retain engine admission,
   owner serialization, stage persistence, exact SHA evidence, local CAS, remote
   leases, and independent normal/replay proofs. Keep re-observations across
   external effects even when they inflate call counts. Fetch and reset are
   effects, and SQLite WAL is not atomic with Git or remote publication.
3. **Repair: recovery runner consistency investigation.** Map callers and
   stop/re-entry behavior of direct `run_git` in control and workspace recovery
   before proposing owner-runner injection. Prove both interrupted and completed
   effect outcomes, including real process restart and unchanged lock ownership;
   do not assume changing runner is behavior-preserving.
4. **Merge: duplicated command construction only when equivalent.** Pilot one
   CAS or worktree-remove call site using the existing primitive, retaining its
   exact expected SHA, options, check/error semantics and owner runner. Compare
   argv and failure outcomes, and exercise competing writer and restart tests.
   Do not merge control, workspace and reconciliation transaction policies.
5. **Extract: only a closed, immutable proof/result seam.** Reconciliation's
   private mutable owner state is the first contract to map, not a reason to
   create more callbacks. Consider a narrowly typed admission/proof result only
   after mapping mutation aliases, WAL stages, normal and replay effects, and
   showing no authority transfer or cycle. Otherwise keep the current placement.
   Measure actual invocation and effect counts before claiming a reduction.

## Remaining evidence gaps

The absent TASK-109 and supplied graph artifacts cannot be cross-checked here.
No complete dynamic effect ledger exists for all workspace preparation,
reconciliation, recovery, hooks, remote transports or concurrent external Git
writers. The historical cleanup trace measures 32 skipped ref-only candidates,
not 32 eligible applications, and inventory discovery can exceed the returned
candidate limit. A future changed-path proof should include real process loss at
each relevant intent/effect/publication boundary and exact-SHA revalidation;
static dependency graphs and callback counts cannot establish those effects.
