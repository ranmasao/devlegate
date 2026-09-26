---
"type": "devlegate.ticket"
"title": "Add a curator-authorized releaser execution role"
---

## Milestone

Post-0.6 release orchestration.

## Goal

Add a specialized releaser execution role for release-preparation tickets while
keeping the final decision to bump a version or originate a release intent under
curator authority by default.

## Context

A normal implementation worker should not be expected to understand project release
policy or to exercise release Git authority.

The intended authority chain is:

```text
curator (human)
  -> authorized release intent / architect ticket
  -> releaser prepares and reports the exact release result
  -> review
  -> accepted
  -> Devlegate performs deterministic authorized release effects
  -> done
```

Agents may analyze policy, recommend a release, or remind a curator that a calendar
release window is due. Such recommendations are not release authority.

Future highly automated projects may deliberately delegate release-intent authority
to an architect agent, but that delegation is not the default and must be explicit
if implemented later.

## Required behavior

- Extend ticket metadata with an explicit executor/role selector or equivalent
  mechanism; ordinary worker remains the default.
- A ticket assigned to `releaser` is executed by the releaser protocol rather than
  the normal implementation-worker protocol.
- Releaser may prepare normal product-tree release changes such as version metadata
  or changelog updates in its isolated execution workspace.
- Releaser receives a typed reporting tool analogous to `devlegate_report`, with
  structured release-specific output rather than imperative Git commands.
- Releaser MUST NOT create/push tags, merge/rebase release branches, mutate canonical
  workflow state, or otherwise own release Git effects.
- The requested release/version is authoritative only when it originates through the
  configured release-authority boundary. Releaser must block/question rather than
  silently substitute a different release.
- Accepted release effects are executed by Devlegate using exact policy, provenance,
  and fail-closed Git rules.
- The design must compose with valid `blocked`/questions handoff through review.

## Acceptance criteria

- Architect/curator can create a release-preparation ticket that deterministically
  selects the releaser executor.
- Releaser can return a typed release proposal/result that is durably bound to the
  execution.
- A mismatch between authorized release intent and observed product/policy state
  cannot be silently corrected by the agent; it becomes a reviewable question or
  failure.
- Reviewer acceptance authorizes Devlegate, not the releaser, to perform exact
  release effects.
- Default release/version authority remains human-curator controlled.
- The role design does not require every project to use releases, version branches,
  or the same version schema.

## Required regressions

- Ordinary tickets without executor metadata still select the normal worker.
- A releaser ticket cannot gain direct Git/tag/workflow mutation authority through
  its role.
- A blocked releaser question follows the same durable review/todo lifecycle as
  other valid worker handoffs.
- Release effects are rejected when the accepted execution cannot be bound exactly
  to the authorized release intent and resolved project policy.
