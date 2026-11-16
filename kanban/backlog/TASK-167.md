---
"type": "devlegate.ticket"
"title": "Design a unified interactive operator repair entrypoint and bounded recovery plans"
---

## Scheduling and scope

**Future iteration after 0.5.7. Backlog only until the curator explicitly
schedules it.** The goal is an architectural contract and bounded implementation
plan for `devlegate repair`, not a 0.5.7 release dependency, an automatic
refactor of the Git architecture story, or a mandate to ship all recovery
backends in one large patch. Any implementation tickets must be finite,
independently testable and explicitly scheduled after the contract is reviewed.
This ticket must produce an actionable design, not an unbounded speculative
framework. Do not introduce persistence migrations for hypothetical customers.

## Problem and operator goal

Today the operator must know whether to invoke `retry` (including forced
retry), `reconcile` (automatic, update-base, resume, published rewrite or
control history), `recover`, `abandonment-recover`, or another recovery
operation, and may have to supply ticket IDs, execution IDs, Git revisions and
operation-specific flags. The right choice depends on Devlegate-owned evidence
and should not require the operator to reconstruct runtime protocol details.

A concrete failure illustrates a missing recovery boundary: the control
history contained a valid review artifact and the expected `review -> todo`
change, but the same commit also created an unrelated new ticket.
`review_provenance._transition` rejected its exact change set with
`review disposition history is conflicting`. The original files and
semantic disposition were not themselves contradictory. Repair required
service shutdown, out-of-band Git history rewriting, remote forced ref
replacement and manual local worktree reset, although an explicitly
authorized narrowly bound provenance exception might have sufficed.
This is a motivating example, **not** permission to waive arbitrary
review-protocol requirements or silently trust agent-authored control data.

## Intended operator experience

One command without mandatory parameters:

```sh
devlegate repair
```

1. **Discover and diagnose.** Check available owner-controlled status,
   blocked work, exact Git/control/product ancestry, runtime intents,
   execution/review provenance, resource identities and relevant receipts.
   Return a bounded list of problems, or "no repair required". Detect
   automatic recovery already in progress and never compete with it.
   The operator selects a candidate only when more than one exists;
   normally no ticket ID, SHA, flag or backend operation name is needed.
2. **Propose a plan.** Show in plain language the observation and evidence,
   candidate repair actions, recommendation, effects and preservation
   guarantees. Separate (a) data loss/irreversible changes, (b) temporary
   disruption/availability, and (c) any weakening of a protocol trust
   assumption. Show what cannot be proved and what remains blocked.
3. **Approve or cancel.** Default to cancellation; provide details and
   alternative bounded plans if meaningful. No mutation from mere
   inspection, no automatic acceptance of a risk because a CLI command
   ran, and no magic `--force` blanket authority.
4. **Revalidate and execute.** Bind approval to the exact observed plan,
   immutable source identities, Git HEADs, relevant runtime/store revision,
   generation/worker identities and resource receipts. Reobserve at
   mutation admission; reject stale approvals with a new preview.
   Perform owner-serialized, WAL/intent/receipt-backed actions via existing
   trusted domain mechanisms; verify effects and report the actual outcome
   or explicit uncertainty. Interrupted operations replay the same intent
   rather than multiplying work.

Interactive terminal selection exists in the current `retry` CLI; clean
reset already has preview, identity/digest binding and re-observation.
Reuse these concepts where sound, without exposing separate low-level
authority through the presentation layer.

## Interface consolidation: retry / reconcile / recover

**Consolidate operator UX, not domain semantics.** Inventory and specify the
exact admissibility and effect guarantees of existing
`retry`, `retry --force`, `reconcile auto`, `update-base`,
`resume`, `control`, `recover` and `abandonment-recover`.
Provide an action-to-repair-plan mapping with source functions,
preconditions, expected effects, journal/replay behavior and refusal
conditions. The recommended plan must use a proven existing mechanism
when one already applies; do not implement a second retry or reconcile
state machine.

The design should make `devlegate repair` the ordinary operator entrypoint
for a blocked/recoverable state. The curator can later decide whether to
remove the old CLI verbs in a **one-shot deliberate CLI change**, after
testing all workflows and auditing internal callers, scripts and documented
usage. Do not retain obsolete aliases or compatibility migrations solely
because they used to exist. Until a replacement is complete, preserve the
original backends and their explicit safety/authority boundaries.

Do not automatically absorb genuinely different operations such as
`drop`, `reset`, arbitrary workspace pruning or release lifecycle changes
under a generic "repair" permission. They may be linked in diagnostics,
but their destructive semantics require separate deliberate scope and
approval; never silently substitute discard/reset for retry/reconcile.

## Minimum-destructiveness and trust

Select the safest valid repair by **preservation and proof**, not only by the
fewest commands or writes. Candidate ranking can generally prefer:
(no mutation) -> prove/recheck current state -> add bounded missing
evidence or exact operator attestation -> reversible local normalization
-> explicit state-changing reconciliation -> irreversible loss/history
rewrite. These are *guidelines*, not an absolute numerical order:
an attestation that waives a security invariant can be riskier than an
ordinary reversible file change. Never weaken CAS, lease, ownership,
WAL ordering, pre-mutation freshness or worker-retirement proof merely
to obtain a smaller-looking plan.

Operator adoption is an **exact exception**, not `trusted=true` in SQLite.
For the motivating review example, distinguish independently:
- valid report identity and review header, fixed canonical ticket blob,
  unambiguous review transition and immutable provenance;
- unrelated extra changes in the same publication commit;
- genuinely conflicting dispositions, ambiguous histories, missing
  reports, forged/changed evidence, unexpected commits or effects.

Only the second class may be considered for a narrow curator-authorized
exception after exact independent checks. A proposed attestation must
bind the source commit, review execution, report/checkpoint/control
identity, full unexpected change set and the specific rule being
excepted. Preserve original Git history and review evidence. Document
where this attestation lives, how its admission is authenticated,
replayed and independently verified, and how the resolver consumes it
without modifying a published review artifact. It must authorize no
future unrelated dispositions or generalized Git bypass.

Terminal interactivity or shared Unix UID alone does **not** authenticate
a human curator: until kernel-enforced Worker isolation/credentials
are available, agents may have same-UID capabilities. Design a
credible operator authority boundary or explicitly refuse such
exceptional adoption when it cannot be established. Do not claim
OS-level isolation that is not present. Lower-risk normal recovery
must remain available without granting a worker new control-plane power.

## Live service and offline maintenance

Prefer existing running service IPC and sole owner state authority.
If the service is stopped or its scheduler cannot safely start, specify
a **bounded maintenance mode under the same exclusive owner lock**,
with no worker launch, scheduler mutations or second independent
writer. Refuse if owner presence, lock handoff, current worker/process
lifetime or state identity is uncertain. Do not bypass an active
owner by opening SQLite or mutating worktrees from a separate CLI
process. After validated repair, restart/resume through the normal
service contract.

Read-only diagnostics must be available even when mutation cannot
be authorized. No TTY means no interactive authorization; machine
output may describe candidates but must not presume consent.

## Required deliverable and phased implementation guidance

Produce a concise, independently reviewable architecture document or
execution report establishing:

1. A **current implementation inventory** of all relevant CLI/IPC/
   runtime recovery paths, their admission and continuation boundaries,
   durable identities, and distinguishing cases. Include a proof for
   why low-level retry/reconcile semantics cannot simply be merged.
2. A proposed typed **RepairProblem / RepairPlan / RepairApproval /
   RepairResult** schema or equivalent minimal representation, with
   bounded inventory, stable plan digest, exact affected resources,
   risk/effect classification, idempotency, single-owner admissibility,
   stale-preview rejection and post-effect uncertainty handling.
   Avoid generic arbitrary-action interpreters, plugin interfaces and
   a duplicate workflow engine.
3. Online/offline authority and trust model, including the exceptional
   operator-adoption case, with explicit limits where operator
   authentication/isolation is insufficient.
4. A concrete CLI dialogue for (a) unambiguous routine retry,
   (b) drifted-base reconciliation preserving WIP, (c) malformed
   review publication containing an unrelated ticket, (d) two
   simultaneous candidates, (e) stale preview / changed worker identity,
   (f) blocked/uncertain/destructive-only alternatives, and
   (g) service-unavailable maintenance.
5. A finite incremental delivery plan: read-only discovery/preview,
   existing-operation delegation and approval, robust offline mode,
   then narrowly scoped explicit provenance adoption (only if
   operator authority is proven). Identify focused unit/real-Git
   crash/restart/IPC tests for each phase and operator-facing
   documentation to replace old command-heavy procedures.

No silent self-approval, repair-only bypass of owner authority, direct
Git ref rewrite, manual SQLite surgery, uncontrolled resource deletion,
or 'success' response before effects are proved. Existing immutable
execution and review records must remain immutable.

## Acceptance and boundaries

The result must make it possible to implement and review a single-command
operator repair experience **without** sacrificing the deterministic
control plane over nondeterministic agents. Cite exact current module /
function boundaries and show what can reuse existing code. Present
unknown or architecturally unsafe cases as explicit blockers, not as
happy-path assumptions. Reviewer should reject any design whose
operator exception lets untrusted worker output authorize future
effects, or whose offline path creates a second active owner.

This is a future architectural specification ticket. Do not implement
a sweeping recovery-system rewrite or change the running service as
part of this ticket, and do not block release 0.5.7, TASK-107/166 or the
independent TASK-165 -> TASK-073 architecture gate.
