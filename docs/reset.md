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
reset operation. With only a ticket ID, an intentional second reset during that
interval is indistinguishable from duplicate delivery and receives the same
receipt. A worker for another ticket is never cancelled; contention
with that worker is reported as a temporary owner blocker before admission.

Workflow refusal is different from an execution blocker. Canonical backlog and
review transitions can return to todo with a durable reset disposition. Accepted
and terminal workflow facts have no reverse requeue transition. Reset never
undoes changes already integrated into product HEAD.

### Amended reviewed specification

For a reviewed execution, publish its attributed `rework` review together with
an unchanged move from review to backlog. Edit the backlog ticket in a separate
commit, then, after the curator decides to schedule it, move the unchanged amended
blob to todo in a third commit. For example, in a clean control checkout using
the default paths (replace the ID and review file with the exact execution):

```sh
git mv kanban/review/TASK-123.md kanban/backlog/TASK-123.md
git add reviews/TASK-123/EXECUTION_ID.md
git commit -m 'Defer TASK-123 with review'
git push origin HEAD:devlegate/control
${EDITOR:-vi} kanban/backlog/TASK-123.md
git add kanban/backlog/TASK-123.md
git commit -m 'Amend TASK-123 specification'
git push origin HEAD:devlegate/control
# After the curator schedules the amended ticket:
git mv kanban/backlog/TASK-123.md kanban/todo/TASK-123.md
git commit -m 'Schedule amended TASK-123'
git push origin HEAD:devlegate/control
devlegate reset TASK-123
```

The reset verifies the immutable review, exact blob transitions and linear
history before it retires the prior generation. Old reports and feedback remain
historical evidence; they are not fed to the fresh worker. An unchanged-spec
rework follows the ordinary review binding instead. A changed ticket or review
after reset is queued blocks fresh admission; preserve the evidence and resolve
the control lineage rather than attempting to replace resources. A failed push
requires fetching and examining the new control history before retrying.

### Ordinary unbound specifications

In a clean control checkout, an operator with control-branch write access can
edit an existing backlog ticket and push the edit as an ordinary commit:

```sh
${EDITOR:-vi} kanban/backlog/TASK-123.md
git add kanban/backlog/TASK-123.md
git commit -m 'Amend TASK-123 specification'
git push origin HEAD:devlegate/control
# Only after the curator schedules the ticket:
git mv kanban/backlog/TASK-123.md kanban/todo/TASK-123.md
git commit -m 'Schedule TASK-123'
git push origin HEAD:devlegate/control
```

An unbound todo ticket can similarly be edited at `kanban/todo/TASK-123.md`,
committed and pushed. The owner reloads the ticket store and re-evaluates
dependencies before selecting work. Keep workflow moves and content edits in
separate commits; preserve the ticket ID and valid ticket schema and DAG.
The owner's first validated control snapshot is its bootstrap boundary: history
before that observation is not retroactively authorized. Once a control head has
been durably handled, later changes are checked against that head, including
across owner restarts. An unsupported transition or protected edit blocks worker
admission; inspect and repair the control lineage before retrying.

| Ticket provenance/state | Operator route |
| --- | --- |
| Backlog or unbound todo | Commit an edit at the existing path; schedule backlog separately. |
| Pending or active execution, recovery, or integration | Do not amend the bound specification; resolve the owner operation first. |
| Review, unchanged specification | Ordinary rework retains its attributed review binding. |
| Review, amended specification | Defer unchanged with an attributed review, amend in backlog, schedule, then reset. |
| Accepted or done | Preserve the reviewed/integrated history; do not edit it in place. |

Do not resolve a rejected push by force-pushing. Fetch and inspect the new
control history, determine whether a worker was admitted or another operation
started, and retry only at a supported boundary. Observation failures, ambiguous
identity, malformed tickets or DAGs, and changed control lineage require repair
and a fresh owner observation. Explicit reset is the resource-retirement route;
an edit alone does not dispose of old worktrees or refs.

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

After retirement of the old worker is proved, reset deliberately discards ordinary
tracked edits, untracked and ignored files, and hidden-index content in the exact
independently bound generation. Initialized nested submodules may have ordinary
source edits too; their HEAD, checkout root and Git administration must still be
proved. Content symlinks are unlinked without following their targets. No old
content is copied into the fresh attempt. Uncommitted edits are not historical
execution evidence; committed code pins and immutable records are preserved.

Unknown nested repositories, special files (such as sockets and FIFOs), mount
boundaries, symlinked Git administration, Git administrative locks, changed
submodule HEAD or foreign module administration remain specific blockers.
Uncertain creation/publication and ambiguous resource bindings also hold
retirement closed. A separate historical worktree namespace is not allocated to
bypass those blockers. Repair must preserve evidence and the exact admitted
resource identity, then repeat the same reset command. These are runtime safety
blockers, not additional workflow eligibility rules. File validation is linear
in checkout and generation-admin entries and never follows content symlinks.
