---
"type": "devlegate.ticket"
"title": "Preserve rework provenance across independent ticket scheduling changes"
---

## Milestone

Devlegate 0.5.8 — blocking self-hosting workflow regression. Complete before rescheduling TASK-182.

## Goal

Allow a previously reviewed ticket to be deferred and rescheduled in ordinary curator control commits that also schedule other tickets, without losing its exact REWORK binding or requiring history repair.

## Context

On 2026-11-25 the service integrated TASK-104 and then blocked before admitting TASK-182 with "cannot bind curator feedback for TASK-182: review disposition history is conflicting". The canonical TASK-182 history is valid: report publication 4c31c86, REWORK to todo with its artifact ebe6ec1, deferral 754fe52 (also scheduling TASK-154 and TASK-184), rescheduling 70f5078 (also scheduling TASK-104). The TASK-182 specification blob was identical at all four boundaries. In review_provenance.py, _transition() projects ticket changes but requires changes == workflow for subsequent scheduling moves, incorrectly treating independent changes as conflicting. TASK-182 has again been deferred while this fix runs, so multiple deferral/rescheduling cycles are part of the real recovery case, not a hypothetical extension.

## Required behavior

- Resolve the existing exact reviewed execution and immutable review artifact; retain atomic review publication/disposition and its existing attribution, specification, ancestry and integrity checks. Subsequent curator scheduling moves are not new review decisions.
- For a deferred REWORK, validate the relevant ticket's canonical workflow paths and the exact specification blob at each scheduling boundary. Independent changes to other tickets in the same commit must not invalidate an otherwise legal todo -> backlog or backlog -> todo move. Use exact canonical path identity, not an arbitrary basename match or a blanket bypass of provenance.
- Allow repeated legal deferral/rescheduling cycles before admission, with or without intervening unrelated commits. Preserve the original review publication and feedback, and distinguish any separately permitted backlog amendment from the specification covered by that historical review. Do not impose an arbitrary one-deferral limit or make a previously proved binding unrestorable solely because the curator scheduled and deferred more than once. Reuse the existing binding and restart/revalidation machinery; keep its evidence sufficient and consistent with its consumers.
- Keep genuine conflicts fail-closed: illegal or ambiguous same-ticket transitions, missing/changed review artifact, unauthorized specification change during a move, or unproved execution/ownership. Do not weaken the rules for lifecycle publication, accepted integration, or active worker admission. Do not reinterpret or rewrite historical review evidence.
- Check other relevant review/scheduling provenance consumers for this same assumption about unrelated ticket changes (including persisted binding and inspection); fix only directly necessary occurrences. Report distinct problems separately rather than expanding this into a general control-history audit or changing unrelated integration semantics.
- Prefer a small change to the existing resolver and its tests, with a concise documentation clarification if necessary. No new state machine, provenance store, compatibility mode, migrations, history rewrite, or TASK-182 special case.

## Acceptance criteria

- A real Git history structurally equivalent to the TASK-182 incident, extended by another deferral/rescheduling cycle, resolves the same exact REWORK artifact and can proceed through normal owner admission when other prerequisites hold.
- Independent scheduling changes do not alter review identity, content, digest, execution association or current/historical specification distinction.
- Persisted bindings remain usable and correctly revalidated across restart after repeated scheduling cycles. Actual corrupt/conflicting evidence still blocks.
- Existing review, inspection and recovery contracts remain intact; focused tests, lint, diff check and exact-checkpoint hosted CI pass.

## Required regressions

1. Real Git: publish exact review -> todo with feedback, move target todo -> backlog while moving other tickets backlog -> todo, then move target backlog -> todo alongside another independent ticket. Resolve exact feedback and verify unchanged specification and correct publication. Include unrelated intervening commits.
2. Repeat the defer/schedule cycle at least once more (including a commit that schedules another task while deferring the target). Exercise persisted binding restore/revalidation and normal owner REWORK admission with original feedback and no fallback to FRESH. Cover direct review -> backlog as well as review -> todo.
3. Independently amend the ticket in backlog, reschedule in a shared commit, defer and reschedule again; retain correct historical-vs-current specification semantics and existing dependency checks.
4. Negative real-Git cases: same-ticket conflicting/duplicate workflow paths, illegal transition, altered blob during scheduling, changed/deleted or wrongly attributed review artifact. Unrelated changes must not mask these errors. Retain existing negative tests and exact review publication constraints.
5. Run focused review provenance, rework handoff, recovery and inspection tests as relevant, ./dev lint and git diff --check. Hosted exact-checkpoint CI is the independent integration gate.

## Non-goals

TASK-182 implementation, actual local runtime recovery or manual Git repair, done -> todo support, generalized lifecycle redesign, reconciliation consolidation, review/publication transaction redesign, broad Git audit or new compatibility paths.
