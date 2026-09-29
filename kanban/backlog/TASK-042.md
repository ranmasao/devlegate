---
"type": "devlegate.ticket"
"title": "Make reconciliation intent-driven and automatically select the safe resolution"
"depends_on": ["TASK-039", "TASK-041"]
---

## Milestone

Devlegate 0.5.6 reconciliation operator UX.

## Goal

Make reconciliation an intent-driven operation with one normal operator command:

```text
devlegate reconcile <ticket-id>
```

The operator should not normally have to decide whether the retained execution needs
`resume`, `update-base`, a same-parent replacement transplant, or a
lease-guarded rewrite of an already published Devlegate execution branch.

The running service must classify the exact reconciliation state deterministically,
choose the strongest safe supported resolution, bind its identities durably, and
either execute it or fail closed.

Additional parameters are assertions/debugging controls, not normal inputs required
to choose the algorithm.

## Context

TASK-039 makes the update-base target service-owned by default and preserves
`--onto` as an optional exact assertion.

TASK-040 proves exact same-parent replacement eligibility.

TASK-041 proves safe transplantation of a linear multi-checkpoint execution lineage,
including an already-published Devlegate-owned prefix, using durable displaced
evidence, one-for-one rewritten-lineage proof, exact force-with-lease publication,
and crash recovery.

The remaining operator surface still exposes implementation choices:

```text
devlegate reconcile resume <ticket-id>
devlegate reconcile update-base <ticket-id>
devlegate reconcile update-base <ticket-id> --rewrite-published
```

Recent live recovery of rslab2 LAB-158 demonstrated that the service already
possesses the facts needed to choose among those paths. Requiring the operator to
select the Git mechanism does not add authority.

Reconciliation remains an explicit operator intent in this ticket. Detecting drift
alone does not authorize history mutation.

## Canonical CLI

Add the common form:

```text
devlegate reconcile <ticket-id>
```

This is the primary documented/operator path.

Given an exact pending reconciliation, the service freshly observes relevant state
and deterministically classifies it.

### Same-base recovery

If the retained execution is still based on the exact current canonical product
generation and existing resume invariants hold:

```text
-> resume
```

### Eligible product movement

If the canonical product generation differs, apply the existing update-base target
eligibility rules.

Supported target classes remain:

- ordinary descendant advancement;
- exact same-parent rewritten product tip from TASK-040.

If eligible, automatically transplant the proven execution lineage onto the exact
fresh current product generation.

### Proven published execution lineage

If transplantation requires moving an already-published Devlegate-owned execution
ref, do not require normal use of `--rewrite-published`.

TASK-041 already supplies the actual authority proof:

- exact Devlegate-owned execution ref;
- exact durable published SHA;
- published SHA is a proven checkpoint/prefix of the retained linear lineage;
- displaced lineage is durably pinned;
- rewritten lineage is proved one-for-one;
- publication uses exact lease/CAS, never unconditional force;
- remote is re-observed exactly;
- crash recovery is write-ahead and idempotent.

If those proofs hold, perform the lease-guarded rewrite automatically as part of the
ordinary reconciliation intent.

If any proof does not hold, fail closed. Do not ask for a force-like bypass flag.

## Deterministic classification

At minimum:

```text
same exact product generation
    -> resume

eligible current product
    + unpublished execution lineage
    -> update-base/transplant

eligible current product
    + exact proven published Devlegate execution prefix
    -> update-base + lease-guarded publication rewrite

unsupported/divergent/ambiguous
    -> remain blocked
```

Classification uses exact Git/state/provenance predicates only.

Never infer equivalence from commit messages, tree similarity, patch-id, timestamps,
authors, reflog, LLM judgment, or semantic guesses.

## Target selection and replay

For automatic update-base:

- resolve current canonical product HEAD inside the running service;
- require expected product branch and clean local/remote agreement;
- bind the exact resolved target to the admitted mutable request before effects;
- same request-ID replay retains the same exact target/result;
- product movement after admission/before effect fails closed.

The operator does not copy a SHA in the normal path.

## Compatibility assertions

Preserve explicit forms where practical:

```text
devlegate reconcile resume <ticket-id>
devlegate reconcile update-base <ticket-id> [--onto <target>]
```

`--onto` remains an exact current-HEAD assertion, not arbitrary target authority.

The existing `--rewrite-published` option may remain temporarily accepted for
compatibility, but it must no longer be required for a proven Devlegate-owned
published-lineage rewrite and must not broaden authority. Do not replace it with
another force/yes/confirm flag.

## Status and acknowledgement

Reuse TASK-039's distinction between request admission and operation completion.

Status/diagnostics must expose the automatically chosen resolution class when known:

- resume;
- update-base;
- update-base with published-lineage rewrite;
- blocked unsupported topology.

Keep the underlying exact target/checkpoint/publication evidence observable.

## Scope boundaries

- One explicit `reconcile` operator intent remains required.
- Do not broaden target eligibility beyond TASK-040/TASK-041.
- No generic force push.
- No arbitrary rebase/merge reconciliation.
- No weakening of workspace, product, provenance, publication, lease, or recovery
  proofs.
- No LLM path selection.
- No generic workflow-policy language.

## Acceptance criteria

- `devlegate reconcile T-1` is sufficient for every currently supported safe
  reconciliation class.
- Same-base pending state automatically selects resume.
- Eligible descendant product selects update-base.
- Eligible same-parent replacement selects the TASK-040 path.
- Proven published execution prefix selects TASK-041 lease rewrite without requiring
  `--rewrite-published`.
- Unsupported/ambiguous topology remains blocked with no weaker fallback.
- Automatic target is fresh, exact, durable, and replay-safe.
- Explicit legacy/assertion forms remain compatible or deliberately deprecated with
  tested behavior.
- Tests, Ruff, and coverage remain green.

## Required regressions

- Same-base pending + `reconcile T-1` -> resume.
- Descendant current product + `reconcile T-1` -> exact current update-base.
- Same-parent replacement + unpublished lineage -> TASK-040 transplant.
- Eligible target + proven published prefix -> TASK-041 lease rewrite without
  `--rewrite-published`.
- Remote moves before lease -> fail closed, no weaker retry.
- Unsupported divergent topology -> remain blocked.
- Product moves between admission and effect -> no unbound retargeting.
- Same request replay after movement -> original bound target/result remains
  authoritative.
- Explicit matching `--onto` -> same service-owned proof path.
- Explicit stale/mismatched `--onto` -> fail closed.
- Legacy `--rewrite-published`, if retained, grants no broader authority.
