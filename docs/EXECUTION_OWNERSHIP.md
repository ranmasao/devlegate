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
