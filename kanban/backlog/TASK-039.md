---
"type": "devlegate.ticket"
"title": "Default reconcile update-base to the current product HEAD"
"depends_on": ["TASK-036"]
---

## Milestone

Devlegate 0.5.6 reconciliation operator UX.

## Goal

Make the common reconciliation command concise:

```text
devlegate reconcile update-base <ticket-id>
```

by defaulting the reconciliation target to the current canonical product HEAD,
while retaining `--onto` as an optional exact operator assertion.

## Context

The current command requires:

```text
devlegate reconcile update-base <ticket-id> --onto <target>
```

but the runtime does not actually allow an arbitrary target. It already requires the
resolved target to equal both:

- the current local product HEAD; and
- the freshly observed product remote HEAD.

Therefore `--onto` currently acts primarily as a manually supplied compare-and-set
assertion for a value Devlegate already has to observe itself.

The normal operator intent is simply:

```text
reconcile this retained execution onto the product generation that is current now
```

Requiring the operator to copy the same HEAD manually adds friction without adding
authority.

## Required behavior

- Make `--onto` optional for `reconcile update-base`.
- With no `--onto`, resolve the target from a fresh canonical product-generation
  observation owned by the running service.
- Do not resolve the default target from stale CLI-side state or from an
  unauthenticated caller checkout.
- The implicit target must still satisfy every existing update-base safety
  invariant, including clean checkout, expected product branch, local/remote
  agreement, execution/checkpoint binding, and any ancestry restrictions that
  remain part of update-base semantics.
- Bind the exact resolved target commit to the admitted mutable operation before
  reconciliation effects are applied.
- Durable mutable-request/receipt semantics must preserve that resolved identity:
  replaying the same admitted request ID must refer to the same effective target,
  not silently re-resolve a newer HEAD.
- If the product generation changes before the operation's final safety
  re-observation, fail closed rather than silently transplanting onto a different
  commit.

### Explicit `--onto`

- Preserve `--onto <target>` as an optional operator safety assertion.
- When supplied, the target must continue to resolve to the exact current canonical
  product HEAD accepted by update-base.
- An explicit target mismatch remains an error.
- `--onto` does not become permission to transplant onto an arbitrary historical
  or unrelated commit.

## CLI and documentation

- Help should describe the default clearly, e.g. that omitted `--onto` means the
  current product HEAD.
- Existing explicit-`--onto` scripts remain compatible.
- Update operations documentation/examples to show the common short form first and
  the explicit assertion form separately.

## Scope boundaries

- Do not broaden which product histories are eligible for update-base in this
  ticket.
- In particular, do not solve rewritten/amended original-base reconciliation here.
- Do not weaken current fail-closed ancestry, checkpoint, workspace, publication, or
  fresh-remote checks.
- Do not change same-base `reconcile resume` semantics.
- Do not make reconciliation automatic merely because the target can now default.

## Acceptance criteria

- `devlegate reconcile update-base T-1` reconciles against the freshly observed
  current canonical product HEAD when the existing update-base invariants permit it.
- The effective target commit is exact, durable, and observable in reconciliation
  state/evidence.
- Same-request replay cannot silently switch to a newer product HEAD.
- A product move between admission and effect fails closed.
- `--onto` remains available as an exact current-HEAD assertion.
- An explicit stale or mismatched `--onto` remains rejected.
- Existing safety semantics for unsupported divergent/rewritten histories are
  unchanged.
- Tests, Ruff, and coverage remain green.

## Required regressions

- Omitted `--onto` + stable eligible current product HEAD -> update-base succeeds.
- Omitted `--onto` + product moves before effect -> no transplant to the new
  unbound HEAD.
- Same admitted request ID replay after product movement -> original resolved target
  remains authoritative or the operation reports its existing durable result.
- Explicit matching `--onto` -> existing behavior succeeds.
- Explicit stale/mismatched `--onto` -> fails closed.
- Rewritten/divergent original base remains subject to the existing update-base
  restriction and is not implicitly accepted by this UX change.
