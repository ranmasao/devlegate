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
- Correct the mutable-request acknowledgement wording. The CLI currently prints
  `reconciliation accepted: <ticket>` once the request/receipt has been admitted,
  even though the service may still reject the reconciliation during execution.
  Use wording that distinguishes request admission from operation success, e.g.
  `reconciliation request accepted: <ticket>; see service log for result`, or an
  equivalent concise message consistent with the existing mutable-request model.
- Do not report a reconciliation as resolved/successful from the client merely
  because the request was durably admitted. Final success remains service-owned and
  observable through status/log/state.

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
- CLI acknowledgement text clearly distinguishes admitted request from resolved
  reconciliation and does not claim success before the service completes it.
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
- Admitted request later rejected by the service -> client acknowledgement says
  request accepted/admitted, not reconciliation succeeded/resolved.


## Review findings

Execution `6179c326db3e4bb3a9ab40b2f5a609e7` / checkpoint
`c4a9159a59211e5b34e3ccbc37c01936ee9f59da` requires a focused
hardening pass.

The implementation direction is appropriate:

- `--onto` is optional at the CLI/IPC boundary;
- omitted target resolution is service-owned;
- admission resolves an exact local/remote-agreed product HEAD;
- the resolved target is persisted in the mutable receipt;
- replay acknowledgement can return the persisted target;
- client wording now distinguishes request admission from completed reconciliation.

No architectural rewrite is requested.

### CI is currently red

GitHub CI run `36584143453` reports:

```text
1 failed, 1041 passed, 1 skipped
```

The failure is the public-help contract:

```text
tests/test_cli.py::test_public_help_surfaces_are_successful_and_useful
```

The test still expects the old phrase:

```text
product branch to use as the new base
```

while the command now documents an exact current product HEAD/default-current-HEAD
assertion. Update the help regression to the intended new contract and rerun full CI
and Ruff.

### Required omitted-target proofs are missing

The candidate changes only existing CLI wording/payload expectations and does not add
the semantic regressions required by TASK-039 for the new service-owned target
selection.

Add strongest-practical-boundary tests for:

1. **Omitted `--onto`, stable eligible product.**
   A real pending reconciliation is submitted without an `onto` field.
   The service resolves the exact current canonical product HEAD, returns/persists
   that SHA in the admission receipt, executes update-base, and records the same SHA
   as `effective_base`.

2. **Target identity is frozen at admission.**
   Admit an omitted-target request and prove the durable receipt contains the exact
   resolved SHA before reconciliation effects. The owner-side operation must consume
   that bound SHA rather than re-resolving "whatever is current" later.

3. **Product moves after admission / before effect.**
   After the omitted-target request is admitted and its target is bound, move the
   canonical product HEAD. The reconciliation must fail closed; it must not silently
   transplant onto the newer HEAD.

4. **Same request-ID replay is target-stable.**
   After an omitted-target request has been admitted, move the product and replay the
   exact same request ID. The replay must return/use the original persisted target
   (or the already durable completed result), not resolve the newer product HEAD.

5. **Explicit target remains an assertion.**
   Preserve/prove the existing matching-`--onto` success and stale/mismatched
   `--onto` failure against the same service-owned current-product rules.

Use the real IPC/owner boundary for admission/receipt/replay semantics where that is
the externally meaningful contract. Engine-level tests may supplement it.

The existing TASK-040/TASK-041 topology and rewrite tests do not need to be
duplicated here.

Return to review with full CI, coverage, and Ruff green.


## Accepted

Execution `811310115fdb4146b7f4edd44e836b2c` / product checkpoint
`c594d577cbce558ae348d29b3f6c98844fe88b6c` satisfies TASK-039.

The final candidate proves the service-owned omitted-target contract at the
IPC/owner boundary:

- an omitted `--onto` request is admitted and resolved to the exact current
  canonical product HEAD;
- the resolved SHA is persisted in the mutable receipt before reconciliation effects;
- owner-side execution consumes the bound target rather than re-resolving a newer
  product generation;
- product movement after admission fails closed and leaves the reconciliation
  pending instead of silently retargeting;
- replay of the same request ID returns the original persisted target;
- explicit target behavior remains compatible;
- public help and request-acknowledgement wording match the new contract.

GitHub CI run `36610943905` is green:

```text
1043 passed, 1 skipped
coverage: 79%
Ruff: all checks passed
```

No further review changes are required.
