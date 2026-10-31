# Execution Ownership Boundary

Each Linux execution has a child cgroup in a delegated cgroup v2 subtree. A
trusted launcher joins that cgroup before `execve()` starts the worker. Every
descendant inherits the cgroup membership, including descendants that call
`setsid()`, change process groups, double-fork, or are reparented.

The execution cgroup, not a PID, PGID, session, or sampled `/proc` ancestry,
is the ownership boundary. Those identities may remain useful for diagnostics
and graceful signaling, but they cannot prove ownership or retirement.

The minimum admitted capability includes `cgroup.kill`. Forced termination
writes that kernel control once, which recursively terminates processes in the
execution cgroup and its descendants without affecting sibling executions.
There is deliberately no parent-only `cgroup.procs` fallback: it cannot prove
recursive termination when a child cgroup is populated.

Retirement is successful only after `cgroup.events` reports `populated 0`.
Leader exit, process-group disappearance, and absence of known PIDs never make
an execution retired. The supervisor waits for this state before destroying
the execution cgroup or reporting orderly shutdown completion.

The capability probe verifies both creation and actual join-before-exec using a
disposable trusted child. It reports missing unified cgroup v2, an unusable or
undelegated subtree, and a usable execution containment capability separately.
Host provisioning is outside this API. On systemd, `Delegate=yes` gives the
service a writable subtree; a future OpenRC or container integration only needs
to provide an equivalent delegated cgroup v2 root.

Worker supervision receives containment through a provider contract. Production
composition uses the Linux cgroup-v2 provider and fails closed when its probe is
not usable. Semantic tests may inject a deterministic provider with the same
create, spawn, terminate, wait, and destroy lifecycle; that test double is not
a production fallback and does not weaken admission requirements.

## Git resource continuity

The execution workspace inspection's `REUSABLE` classification describes Git
topology and workspace state; it is not a generation-specific ownership proof.
A worktree and its branch can both be removed and recreated at the same path,
branch and commit while producing the same inspection result. In particular, a
report or an admission record naming that commit does not establish continuity
of either resource. The same limitation applies to an upstream ref observed at
the same object ID. A separate marker or evidence ref that survives replacement
of the resource cannot by itself establish that the resource was not replaced.

Historical executions have no owner-published resource-continuity binding and
must therefore be treated as unsafe by any generic reset proof. Existing
workspace recovery classifications and checkpoint pins are not such a binding.

New admissions persist a nonce-bearing resource intent alongside the pending
execution state, before workspace preparation. On the first ordinary preparation,
when the owner creates both the branch and worktree, it writes the admission
identity and nonce into a new file in the Git worktree administrative directory,
syncs it, and journals the observed creation before worker launch. Replayed or
pre-existing resources are not assigned a marker. An interruption before the
creation event leaves only incomplete evidence, even if a marker exists. The
marker and journal are not yet ownership proof: publication, ref mutations and
retirement are not comprehensively journaled. A pending execution predating the
intent is not assigned one on replay.

Fresh preparation additionally records separate intent and verified events for
branch creation, worktree creation, and marker installation. Each verified event
follows a Git or marker observation, and a failed journal write stops the next
effect. An unmatched intent is ambiguous after restart; neither it nor a complete
creation sequence alone grants authority. Other mutation paths remain outside
this journal, and same-OID ref replacement is not observable without exclusive
owner mediation.

A future resource proof must assume that resource mutations are mediated by the
serialized owner. Git cannot retrospectively detect an out-of-band deletion and
recreation of a ref at the same object ID. Detectable drift and incomplete or
unknown state must fail closed; stronger continuity requires an enforced
exclusive-writer boundary.

### Remaining transition-journal dependency

Positive proof depends on a single owner-serialized journal covering every
mutation of the execution resources, not just normal workspace preparation.
The current paths include `ExecutionWorkspaceManager.prepare` and
`prepare_rework`, `repair_bound_workspace`, checkpoint publication and rework
predecessor publication, reconciliation rewrites, and the runtime's retirement
and automatic cleanup paths. Several of these intentionally reuse, move, or
remove an existing branch. They cannot safely be interpreted as fresh creation
from their present path and commit observations.

Implementation must first record a transition intent before each Git effect,
then durably record its verified result before the next effect or worker
launch. Each intent must identify the generation nonce, exact resource and
expected predecessor; ref updates and deletes require exact leases or CAS.
Fresh creation must refuse pre-existing unbound resources rather than adopt
them. A worktree-specific marker in its Git administrative directory can tie
the journal to the observed registration, but cannot establish ref continuity
on its own. Ref continuity additionally depends on exclusive owner mediation;
Git cannot detect a delete and recreate at the same object ID by another writer.

Recovery must leave an interrupted transition unproven unless its outcome can
be established without adopting a foreign resource. Retirement must revoke the
old generation before removing or transferring any resource. Rework, retries,
reconciliation, and automatic cleanup must use the same protocol before the
proof API can return positive results. Tests must exercise interruption on both
sides of each effect and journal write, including remote publication, and show
that prior reports and evidence pins are retained. Until that integration is
complete, the read-only proof remains negative for every generation.
