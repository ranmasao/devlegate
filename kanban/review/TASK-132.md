---
"type": "devlegate.ticket"
"title": "One-argument reset: stop active worker and start same ticket from a fresh execution"
"depends_on": ["TASK-131", "TASK-162"]
---

## Operator contract — authoritative revision 2026-11-13

The only *task input* to the public reset operation is the canonical ticket ID:

```sh
devlegate reset TASK-XXX
```

No mandatory `--plan-digest`, `--generation`, `--request-id`, prior `inspect` call, interactive confirmation, execution ID, or explicit worker-stop command. Conventional **optional output-format switches** (for example `--json`) do not supply mutation authority. `inspect` is an independent, universal read-only diagnostic, never a precondition for reset.

**User goal:** start the *same ticket* anew with no previous worker/code/rework continuation, especially when changing to a more capable model after unproductive iterations. This is a lifecycle operation, **not** a manual resource-deletion interface or an alias for `workspace prune`.

The sole **workflow eligibility rule** is: the ticket is already in `todo`, or can safely be transitioned into `todo` by the canonical workflow. Do **not** hard-code `backlog` as the only allowed source, or automatically forbid review/accepted/done/abandoned by a separate reset-specific status list; evaluate the canonical legal transition and its audit requirements. If an existing terminal/integrated ticket cannot lawfully be requeued, report that workflow reason. Do not silently change historical acceptance/integration facts. A done ticket already integrated into product HEAD is **not** undone by reset.

Infrastructure failures (e.g. unsafe/uncertain Git state, inability to stop a worker, unavailable remote when an exact remote deletion is required, storage conflict) are **execution blockers**, not new eligibility arguments or a reason to demand identity hashes from the operator. Report and safely recover them; never claim a successful fresh start while the old owner may still execute.

## Required owner-serialized lifecycle

1. **Resolve the canonical ticket and workflow status internally.** Under the existing service owner, reobserve committed control state and exact control lineage. Determine whether the ticket is in `todo` or can be lawfully returned to it. Reject unknown/foreign/ambiguous ticket identity and a forbidden transition with a clear diagnostic. Do not require a user-supplied digest or preflight preview.
2. **Persist a durable reset request before interruption/effects.** Generate the operation ID internally, and derive the relevant execution ID, generation, resource binding, current preview/digest and control revision from authoritative state *at admission*. Use immutable/internal identity bindings, not just a name/SHA guess. Only one reset intent per ticket/lineage may be active; concurrent/duplicate requests observe or resume that same intent rather than racing or widening disposal scope. Retain enough receipt/identity to resume after a lost acknowledgement without an operator-provided request ID.
3. **Stop an active worker for this ticket.** Quiesce scheduling and prevent any further old-worker writes/checkpoints. Request the managed worker to terminate through existing owner/process-lifetime supervision; escalate through the existing bounded process/cgroup retirement mechanism when needed. **Verify retirement of the entire managed process group/descendants and release of ownership before touching its execution resources or starting a replacement.** If retirement cannot be proved, remain blocked and resumable. No orphaned live agent, double execution or hidden cancellation of another ticket.
4. **Discard lineage, not historical evidence.** The new attempt must not reuse any old execution worktree content, checkpoint, worker conversation/session, inherited rework feedback, prompt-specific retries or old resource generation. Retain prior immutable execution reports, review/acceptance/audit records and pinned resource evidence; if a worker is interrupted mid-execution, record an accurate durable reset/cancellation disposition without mislabeling it completed. New work starts on **current integrated product HEAD** with current model/agent configuration.
5. **Safely resolve old resource namespace.** Reuse the exact ownership and CAS/lease-protected staged retirement/WAL/replay implementation from TASK-130/131 where applicable, but **derive all confirmation/generation/request identities internally**. If prior resources cannot be proved, fail closed on destructive effects; do not adopt historical unbound generations, silently force Git cleanup, overwrite a foreign ref or mistake observation failure for absence. Consider whether resources can remain safely as preserved historical evidence while a truly fresh, nonconflicting generation is allocated; if so prove namespace separation first. A read-only local snapshot cannot authorize deletion of a mutable remote ref; remote must be observed when an actual remote effect requires it.
6. **Requeue and activate ordinary scheduling.** Complete the durable, CAS-guarded control transition to `todo` (or preserve `todo` status) with the *same ticket specification/blob* and unambiguous historical disposition. No automatic mutation of completed product changes. Only after retirement/cancellation and resource isolation are proven may the normal owner scheduler admit a fresh execution ID, generation and worktree. The reset command itself must make this inevitable at the next owner scheduling opportunity **without a second operator command**; if immediate start cannot be confirmed, report accurately as queued/in-progress rather than already running or completed. Do not bypass scheduler ownership or start a second competing worker directly from CLI.
7. **Expose clear status and replay.** `reset TASK-XXX` can report `already fresh/no-op`, `stopping-worker`, `retiring-old-generation`, `queued`, `started` or `blocked` as grounded in durable state; distinguish admission from fully established fresh execution. Re-invoking the same one-argument command during an in-progress reset returns progress or resumes it safely. A crash/restart resumes from the internal durable identity, including uncertain Git operation outcomes, without demanding an external request ID. An already-`todo` pristine ticket with no active/retained attempt should be a safe idempotent no-op or normal scheduling nudge, not a destructive reset.

## Tests and validation

Prove on disposable real-Git and process-lifetime fixtures:

- Starting from backlog, and already-todo with/without an active worker; lawful other workflow transitions versus explicitly forbidden requeue; preservation of exact ticket blob and past review history.
- Kill/cancel an active managed worker, including stubborn descendants, lost acknowledgement, worker stop vs attempt to create new checkpoint, restart in the middle of cancellation, and failure to prove process retirement (must block).
- Repeated `reset TASK-XXX` during one in-progress operation, competing calls and restart with **no user-provided request ID**; no second simultaneous worker, no unrelated-ticket cancellation.
- Exact locally derived resource identity and stale/replaced/missing generations; staged worktree/local/remote ref CAS and lease races, locks, dirty/untracked files and nested submodules; remote unavailable where required; preservation of unknown resources and independent historical evidence.
- A new worker truly receives **current** product HEAD/model configuration and an original-ticket prompt without past code/checkpoint/rework continuation. Verify fresh execution ID and generation and that no extra manual `start`, `retry` or `inspect` is needed.
- Public CLI accepts a single ticket argument and does **not** require digest, generation, request ID or confirmation; errors distinguish a prohibited workflow transition from temporary safety/runtime blockers.
- Full configured CI, Ruff and focused real-Git tests. Report effect/cost bounds, exact remaining gaps and operator recovery documentation.

## CLI integration and compatibility boundary

Replace/remove legacy public `devlegate clean apply`, `clean status` and remaining `clean` domain. Respect integrated `devlegate inspect {ticket,execution,workspace,service}` and `devlegate workspace {ls,prune}` (TASK-161/162). Do not add `reset inspect`, `gc`, mandatory manual confirmation or a long-lived compatibility shim.

## Scope and safety

TASK-132 owns the finished high-level reset contract; do not retroactively edit accepted TASK-130/131 execution evidence. No migrations or generic VCS abstraction. Preserve WAL/revision-CAS, exact ResourceBinding, owner serialization, remote exact-ref lease and fail-closed semantics. Destructive testing only in disposable repositories/process groups; no effect on live operator resources, TASK-073/TASK-126, historical TASK-129 or TASK-131. Preserve TASK-073 human-only acceptance and do not self-accept TASK-126.
