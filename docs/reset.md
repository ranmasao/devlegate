# Reset a ticket

```sh
devlegate reset TASK-XXX
devlegate reset TASK-XXX --json
```

The canonical ticket ID is the only task input. The owner derives the operation,
execution and resource identities internally. Inspection is an independent
read-only diagnostic. No prior inspection, digest, generation, request ID,
confirmation or separate worker-stop command is required.

Reset preserves the specification and historical execution/review evidence. It
discards continuation authority for the old attempt, retires its exact resource
namespace, and queues the original ticket for ordinary owner scheduling. A fresh
attempt gets a new execution ID and resource generation, current product HEAD,
and the model/agent configuration effective at fresh admission. The old worker
cannot publish another checkpoint after its cancellation has been admitted.

## Progress and recovery

The command reports durable progress:

- `already-fresh`: a pristine todo ticket needs no destructive reset; scheduling
  was nudged.
- `stopping-worker`: the reset request is durable; managed worker retirement is
  pending.
- `retiring-old-generation`: worker retirement is proved; exact resource
  retirement is pending.
- `queued`: cancellation and resource retirement are proved, the same ticket
  blob is in todo, and the normal scheduler can admit a fresh attempt.
- `started`: the supervisor proved that a fresh worker started. It may already
  have finished; this status does not assert that it is still running.
- `blocked`: the response includes the durable stage and diagnostic. It does not
  claim a fresh worker exists.

Repeat the same command after a lost acknowledgement or temporary failure. A
service restart restores the internally generated operation identity and pending
resource stages. Do not replace the worktree, binding files, or refs to try to
make a blocked observation pass. Preserve the diagnostic and evidence while
repairing connectivity, storage, Git locks or process supervision. Unknown or
replaced resources remain untouched. A changed control lineage requires repair
of the admitted owner transaction rather than a new disposal scope.

While the just-established fresh worker is still executing with unchanged
configuration, repeating reset returns that operation's `started` receipt. It
does not cancel the replacement after a lost acknowledgement. Changing the
worker configuration, or subsequent durable attempt progress, permits a new
reset operation. A worker for another ticket is never cancelled; contention
with that worker is reported as a temporary owner blocker before admission.

Workflow refusal is different from an execution blocker. Canonical backlog and
review transitions can return to todo with a durable reset disposition. Accepted
and terminal workflow facts have no reverse requeue transition. Reset never
undoes changes already integrated into product HEAD.

## Effects and cost

Only one owner serializes admission, cancellation, resource retirement and
scheduler activation. A managed cancellation uses the existing bounded graceful
termination and containment escalation. An unproved retirement holds scheduling
closed. Resource retirement writes a stage before each effect and reobserves
exact postconditions before advancing. It removes at most one independently
bound worktree, one local ticket ref by expected-old CAS, and one exact upstream
ref by lease. Immutable report/review records, code pins and generation receipts
are retained. At most one new local immutable execution code pin is created if
the old generation does not already have one. Remote observation failure never
means absence.

The implementation uses Git administrative and recursive submodule checks. Its
observation cost scales with repository history, report count and worktree size;
it is not a constant-time garbage collector. There is no namespace sweep, bulk
ref deletion, automatic lock override, or double-force worktree removal.

Dirty/untracked/ignored files, hidden index flags, uncertain creation/publication,
and ambiguous resource bindings currently hold retirement closed. A separate
historical worktree namespace is not allocated to bypass those blockers. Repair
must preserve the evidence and the exact admitted resource identity. These
checks are runtime safety blockers, not additional workflow eligibility rules.
