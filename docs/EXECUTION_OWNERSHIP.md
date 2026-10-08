# Execution Ownership Boundary

Worker processes are launched in a dedicated session and process group. The
group identity is protected by PID start time, boot ID, and exact PGID/SID
checks, so forced interruption does not target a reused PID or PGID.

The descendant tracker is additional best-effort cleanup. It records
descendants observed through `/proc` before they are reparented and validates
recorded identities fail closed. A `/proc` PPID walk is not a recursive
ownership boundary: a worker can fork, have the child call `setsid()`, and
exit before the next walk. After reparenting, the child is no longer
distinguishable from an unrelated process using the available process-group
and ancestry primitives.

Consequently, `worker_group_retired` proves retirement of the recorded worker
process group and all observed descendant identities, not absence of every
execution descendant in the service cgroup. Since that distinction is not a
safe execution retirement proof, the supervisor currently fails closed when
the tracker is active instead of reporting retirement. A known observed
descendant or an identity inspection failure also keeps retirement unproven.
Cleanup of observed descendants remains bounded by exact PID/start-time
identity checks and never signals a stale PID or unrelated process.

The 2026-10-06 left-over-process incident is consistent with this limitation:
an escaped child can remain in the service cgroup after the worker group is
gone, while systemd later cleans it up at service stop. A focused follow-up is
required to place each execution in its own systemd scope/cgroup and use that
scope as the recursive ownership boundary. That work must define scope
creation, membership, forced-stop signaling, retirement observation, and
protection against stale scope/PID identity before strengthening orderly-stop
claims. This ticket intentionally does not redesign service-manager
integration.

The daemon's `orderly shutdown complete` log means that the daemon has
finished its own orderly shutdown path and does not claim that systemd's
service cgroup is empty. Systemd remains the final service-level containment
boundary until the execution-scope follow-up supplies a recursive boundary.
