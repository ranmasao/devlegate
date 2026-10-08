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
