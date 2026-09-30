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


## Review findings

Execution `636a1992b3644198b1842c424aa1cd4b` / checkpoint
`424b40987b381bbed157d96a2f11e74c2b3efb9b` requires a focused
hardening pass.

The implementation direction is correct:

- the canonical CLI form `devlegate reconcile <ticket-id>` is introduced;
- the request is service-owned through a dedicated mutable IPC operation;
- same-base state is intended to dispatch to resume;
- moved eligible product state is intended to dispatch to update-base;
- automatic update-base reuses TASK-041's proven lease-guarded published rewrite;
- legacy explicit reconciliation forms remain available.

Do not redesign TASK-040/TASK-041 or add broader Git heuristics.

### Semantic blocker: `--onto` currently selects the algorithm

In `_validate_reconcile_auto_admission()`, any non-null `command.onto`
immediately delegates to `_validate_reconcile_admission()`, and
`_dispatch_operator_command()` selects resume versus update-base solely from
whether `command.onto is None`.

That overloads one field with two different meanings:

1. operator assertion: "I expect the canonical current product HEAD to be H";
2. service classification: "this reconciliation requires update-base".

Those meanings must remain independent.

The TASK-042 contract requires `--onto` to be an assertion only. For example:

```text
pending reconciliation
current product == original base == H
devlegate reconcile T-1 --onto H
```

must validate the assertion and still classify as **resume**. Supplying an exact
assertion must not force update-base.

Likewise, for a moved eligible product, an explicit matching `--onto H` should
validate H and then select update-base because the observed topology requires it,
not because the option was present.

Persist the selected reconciliation class independently from the optional assertion
/ resolved target identity, and dispatch from that durable classification. Reuse
the existing reconciliation state if practical; do not add a generic policy layer.

### Required TASK-042 regressions are missing

The checkpoint changes only production files. The full suite still collects the
same 1043 tests as before; no TASK-042-specific test coverage was added.

Add regressions at the strongest practical boundary for the public automatic path:

1. **Canonical same-base -> resume**
   `devlegate reconcile T-1` on a same-base pending reconciliation selects and
   completes resume.

2. **Explicit matching assertion does not select update-base**
   Same-base + `devlegate reconcile T-1 --onto <exact-current-H>` still selects
   resume.

3. **Descendant current product -> update-base**
   Canonical reconcile selects update-base against the exact service-observed HEAD.

4. **Same-parent replacement -> TASK-040 path**
   Canonical reconcile succeeds through the already-proven same-parent eligibility
   without the operator selecting update-base.

5. **Proven published prefix -> TASK-041 rewrite automatically**
   Canonical reconcile performs the exact lease-guarded rewrite without requiring
   `--rewrite-published`.

6. **Lease drift / remote movement**
   Automatic published rewrite fails closed under exact lease mismatch and never
   retries with weaker/unconditional force.

7. **Unsupported divergent topology**
   Canonical reconcile remains blocked and does not guess another strategy.

8. **Admission/effect product movement**
   The automatically bound target remains exact; later product movement causes
   fail-closed behavior rather than retargeting.

9. **Same request-ID replay**
   Replay preserves the originally classified/bound operation identity and target
   rather than reclassifying against a newer product generation.

10. **Explicit stale/mismatched `--onto`**
    Fails as an assertion while leaving the service-owned classifier semantics
    unchanged.

Include at least one CLI/IPC-level proof that the user-facing
`devlegate reconcile T-1` form reaches the automatic service path, rather than
testing only internal engine methods.

### Public documentation is missing

TASK-042 says the canonical form is the primary documented operator path, but this
checkpoint changes no README/operations documentation.

Update the relevant operator documentation so the normal form is:

```text
devlegate reconcile <ticket-id>
```

and describe `resume`, `update-base`, `--onto`, and
`--rewrite-published` as compatibility/diagnostic details rather than required
normal decision inputs.

### CI

GitHub CI run `36619542377` has:

```text
1043 passed, 1 skipped
coverage: 79%
Ruff: failed
```

Ruff failure:

```text
src/devlegate/runtime.py:7984:89 E501 Line too long
```

Fix the lint error and return with full CI, coverage, and Ruff green.


## Second review hardening

Execution `d89673f2776d4dcbaf74bec7df892db8` / checkpoint
`c42783762b70668fb3c29b709898852774bfa59c` resolves the previous
algorithm-selection defect:

- `resolution_class` is now distinct from the optional `--onto` assertion;
- same-base state can remain `resume` even when an assertion is supplied;
- dispatch uses the durable selected class rather than presence/absence of
  `command.onto`;
- operator documentation now presents `devlegate reconcile <ticket-id>` as the
  canonical form;
- GitHub CI run `36627606981` is green:
  `1045 passed, 1 skipped`, coverage 79%, Ruff green.

Two focused items remain before acceptance.

### Normalize accepted commit prefixes to full Git identities

The user-facing `--onto` assertion is resolved with:

```text
git rev-parse --verify <value>^{commit}
```

so a unique hexadecimal commit prefix is valid Git input. Git requires at least
four hexadecimal characters for an abbreviated object name.

Make that contract explicit and keep durable provenance canonical:

- document in the relevant CLI help that a commit may be supplied as a full SHA or
  a unique hexadecimal prefix of **at least 4 characters**;
- reject shorter hash-like prefixes with a clear operator-facing error rather than
  relying on an opaque downstream Git failure;
- once a prefix is accepted, normalize it immediately to the resolved full
  40-character commit ID;
- persist/acknowledge/use the full resolved commit ID in mutable receipt and
  reconciliation state, not the raw user prefix;
- a replay must therefore return the same canonical full commit identity;
- ambiguous/nonexistent prefixes still fail closed.

This applies to the public reconciliation assertion surface that actually accepts
abbreviated Git commit identities. Do not broaden other commands that currently
require exact 40-character identities merely for consistency.

Add focused regressions for:

- 4+ character unique prefix -> accepted and normalized to full SHA;
- 1-3 character hash-like prefix -> explicit rejection;
- ambiguous 4+ prefix -> rejection;
- replay after prefix admission -> full original SHA remains authoritative.

### Prove the automatic classifier itself

The new checkpoint adds two CLI routing tests, but no test currently exercises the
new service-side automatic classifier/dispatch semantics directly. The existing
TASK-040/TASK-041 tests prove the underlying update-base/rewrite mechanisms; they do
not prove that `reconcile-auto` selects those mechanisms correctly.

Do not duplicate the entire old topology matrix. Add a compact focused set around
the new classifier/dispatch boundary proving at least:

- same-base -> durable `resolution_class=resume` -> resume dispatch;
- same-base + matching `--onto` assertion -> still resume;
- moved eligible product -> durable `resolution_class=update-base` and exact full
  target -> update-base dispatch;
- moved product + proven published execution predecessor -> selected published-lineage
  rewrite class and TASK-041 path is invoked without operator rewrite authorization;
- unsupported/divergent product -> admission fails closed before a weaker dispatch;
- same request-ID replay returns the previously persisted class/target without
  reclassification.

The tests may reuse existing TASK-040/TASK-041 fixtures/helpers. The purpose is to
prove the **new selector and durable binding**, not to re-prove every internal Git
rewrite invariant.

Return to review with full CI, coverage, and Ruff green.


## Third review: final proof gaps only

Execution `9c3fea0e8eec42d89eaff62fe5e56841` / checkpoint
`c6908be306857d701a6ff8e730287d16205be9af` correctly implements the
requested production behavior:

- `--onto` accepts a full SHA or unique hexadecimal prefix of at least 4
  characters;
- 1-3 character hexadecimal prefixes are rejected explicitly;
- accepted assertions are normalized to the full commit ID;
- classifier selection remains independent from assertion presence;
- help and operator documentation describe the prefix contract;
- focused classifier tests cover resume, update-base, and published-lineage rewrite
  dispatch;
- GitHub CI run `36676203701` is green:
  `1049 passed, 1 skipped`, coverage 79%, Ruff green.

No further production-code redesign is requested.

Two regressions explicitly required by the previous review are still absent and are
the only remaining blockers:

1. **Ambiguous 4+ hexadecimal prefix fails closed.**
   Construct two reachable Git objects/commits sharing a sufficiently short
   4+ prefix, or deterministically discover an ambiguous prefix in the fixture,
   then prove the reconciliation assertion is rejected. Do not mock away Git's
   ambiguity result.

2. **Canonical auto receipt survives restart/replay without reclassification.**
   Admit a real `reconcile-auto` request using a short unique prefix, and prove
   before effect that the durable mutable receipt contains:
   - the full 40-character resolved `onto`;
   - the selected `resolution_class`.

   Reconstruct `ServiceEngine` from disk and replay the same request ID with the
   same original request semantics. The acknowledgement must return the same full
   SHA and class, no new operator command may be queued, and the service must not
   re-observe/reclassify against a changed product generation.

The existing TASK-039 tests already cover generic update-base target freezing and
post-admission product movement; do not duplicate those. This pass should be tests
only unless they expose a defect.

Return to review with full CI and Ruff green.


## Accepted

Execution `87396ffbf531401dbad164265aa56dab` / checkpoint
`0c4ca8e0deadca459fb204a273eae8ed9995aed5` completes the final
TASK-042 proof gaps.

The accepted implementation now proves:

- canonical `devlegate reconcile <ticket-id>` service-owned classification;
- assertion presence does not choose the reconciliation algorithm;
- resume, update-base, and published-lineage rewrite are distinct durable
  resolution classes;
- `--onto` accepts a full SHA or unique hexadecimal prefix of at least 4
  characters and normalizes accepted input to the full 40-character commit ID;
- ambiguous commit prefixes fail closed through real Git resolution;
- the full canonical target and `resolution_class` are persisted in the mutable
  receipt;
- restart/replay of the same request ID returns the original target/class without
  product re-observation, reclassification, or duplicate command admission;
- operator documentation and CLI help expose the intended canonical interface.

The final hardening pass is tests-only relative to the prior implementation
checkpoint.

GitHub CI run `36679325462` is green:

```text
1051 passed, 1 skipped
coverage: 79%
Ruff: all checks passed
```

No further review changes are required.
