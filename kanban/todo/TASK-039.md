---
"type": "devlegate.ticket"
"title": "Make reconciliation intent-driven and automatically select the safe resolution"
"depends_on": ["TASK-036", "TASK-041"]
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

The current reconciliation surface exposes implementation choices to the operator:

```text
devlegate reconcile resume <ticket-id>
devlegate reconcile update-base <ticket-id> --onto <target>
devlegate reconcile update-base <ticket-id> --onto <target> --rewrite-published
```

Recent live recovery of LAB-158 demonstrated that the service already possesses the
facts needed to choose among these paths:

- exact pending ticket/execution/reconciliation identity;
- original product base;
- preserved worker checkpoint;
- fresh canonical product local/remote HEAD;
- execution workspace/branch binding;
- exact published execution remote identity;
- linear checkpoint lineage and reconciliation evidence refs;
- target eligibility rules from TASK-040;
- lease-guarded published-lineage rewrite and crash recovery from TASK-041.

Requiring the operator to restate those facts or select the Git mechanism does not
add authority. It creates opportunities to choose the wrong subcommand or copy the
wrong SHA.

Reconciliation remains an explicit operator intent in this ticket. Detecting product
drift does not by itself authorize Devlegate to mutate history without a reconcile
request. A future project policy may choose a more automatic trigger, but that is
outside this task.

## Canonical CLI

Add the common form:

```text
devlegate reconcile <ticket-id>
```

This is the primary documented/operator path.

Given a pending reconciliation, the service must freshly observe the relevant state
and classify it without LLM judgment or heuristic similarity.

### Same-base recovery

If the retained execution is still based on the exact current canonical product
generation and the existing resume invariants hold, automatically perform the
existing safe resume resolution.

Conceptually:

```text
pending reconciliation
product == original/effective base
exact workspace/checkpoint binding
-> resume
```

### Product advancement / eligible replacement

If the current product generation differs from the retained execution base, resolve
the current canonical product HEAD inside the service and apply the existing
update-base eligibility rules.

This includes the already-supported cases:

- ordinary descendant advancement;
- exact same-parent rewritten product tip from TASK-040.

Do not broaden target topology in this ticket.

When eligible, automatically transplant the proven execution lineage onto the exact
fresh current product generation.

### Published execution lineage

If update-base requires moving an already published Devlegate-owned execution ref,
do not require a normal operator to add `--rewrite-published`.

TASK-041 already requires all of the actual safety authority:

- exact Devlegate-owned execution ref;
- exact published SHA bound in durable state;
- remote SHA is a proven checkpoint/prefix of the retained linear execution lineage;
- displaced lineage is durably pinned as provenance evidence;
- rewritten lineage is proved one-for-one;
- remote mutation uses exact lease/CAS, never unconditional force;
- remote is freshly re-observed after publication;
- crash recovery is write-ahead and idempotent.

If those proofs hold, the service may perform the lease-guarded rewrite as part of
the ordinary reconciliation request.

If any proof does not hold, fail closed. Do not ask the operator to bypass the proof
with a stronger-looking force flag.

## Deterministic classification

The service owns classification. At minimum distinguish:

```text
same exact product generation
    -> resume

eligible current product generation
    + unpublished execution lineage
    -> update-base/transplant

eligible current product generation
    + exact proven published Devlegate execution prefix
    -> update-base + lease-guarded publication rewrite

unsupported/divergent/ambiguous topology
    -> remain blocked with exact diagnostic
```

Classification must use exact Git/state/provenance predicates only.

Do not infer equivalence from:

- commit message;
- tree similarity;
- patch-id;
- timestamp/author;
- reflog;
- LLM judgment;
- semantic guesses about the changes.

## Target selection

For automatic update-base:

- resolve the target from a fresh canonical product-generation observation owned by
  the running service;
- require the expected product branch and clean local/remote agreement;
- bind the exact resolved target to the admitted mutable request before effects;
- same request-ID replay must keep the same bound target/result and must not silently
  re-resolve a newer product HEAD;
- movement after admission/before effect fails closed.

The operator must not need to copy a SHA in the normal path.

## Advanced/compatibility assertions

Preserve the existing explicit reconciliation forms where practical for scripts and
diagnosis:

```text
devlegate reconcile resume <ticket-id>
devlegate reconcile update-base <ticket-id> [--onto <target>]
```

Their safety semantics remain exact.

`--onto <target>`, when supplied, is an assertion that the operator expects this
exact current canonical product generation. It is not permission to reconcile onto
an arbitrary historical target.

The existing `--rewrite-published` option may remain temporarily accepted for CLI
compatibility, but it must no longer be required for a proven Devlegate-owned
published-lineage rewrite and must not weaken or change the proof set. Document it as
unnecessary/deprecated if retained.

Do not introduce a replacement force/yes/confirm flag for the same proven case.

## Request acknowledgement

Correct the mutable-request acknowledgement wording.

The client currently can print wording such as:

```text
reconciliation accepted: LAB-158
```

after request admission even though the service may subsequently reject the
operation.

Client acknowledgement must distinguish request admission from operation success,
for example:

```text
reconciliation request accepted: LAB-158; see service log/status for result
```

or an equivalent concise wording consistent with the mutable-request/receipt model.

Do not claim `resolved` or successful reconciliation merely because the request
was admitted. Final result remains service-owned and observable through status/state
and operational logs.

## Status and diagnostics

Expose enough human-readable state to explain the automatic choice/result, including
the effective resolution class when known, for example:

- resume;
- update-base;
- update-base with published-lineage rewrite;
- blocked unsupported topology.

Do not require the operator to reconstruct why a command selected a path from raw
Git SHAs alone.

Structured state/evidence should retain the exact target, old/new checkpoint,
publication identity, and resolution already required by the underlying operations.

## Scope boundaries

- Do not automatically reconcile merely because drift was detected; one explicit
  reconcile intent remains required.
- Do not broaden target eligibility beyond the rules already implemented by
  TASK-040/TASK-041.
- Do not add generic force push.
- Do not add arbitrary rebase/merge reconciliation.
- Do not weaken clean-workspace, product local/remote agreement, checkpoint,
  workspace, provenance, publication, lease, or crash-recovery proofs.
- Do not use an LLM to choose the reconciliation path.
- Do not create a generic workflow-policy language in this ticket.

## Acceptance criteria

- `devlegate reconcile T-1` is sufficient for every currently supported safe
  reconciliation class.
- Same-base pending state automatically selects resume.
- Eligible current product advancement automatically selects update-base.
- Eligible same-parent product replacement automatically selects the TASK-040
  transplant path.
- A proven published Devlegate execution prefix automatically selects the TASK-041
  lease-guarded rewrite without requiring `--rewrite-published`.
- Unsupported or ambiguous topology remains blocked and receives no weaker fallback.
- The automatic target is freshly service-resolved, exact, durable, and replay-safe.
- Explicit `--onto` remains a current-HEAD assertion rather than target authority.
- Existing explicit subcommands remain compatible unless a deliberate parser
  deprecation is documented and tested.
- Client acknowledgement distinguishes admitted request from completed resolution.
- Tests, Ruff, and coverage remain green.

## Required regressions

- Pending same-base execution + `reconcile T-1` -> resume selected and resolved.
- Descendant current product + `reconcile T-1` -> update-base selected against the
  exact fresh product HEAD.
- Same-parent rewritten current product + unpublished lineage -> automatic
  TASK-040 transplant.
- Same-parent/eligible target + proven published execution prefix -> automatic
  TASK-041 lease-guarded rewrite with no `--rewrite-published` flag.
- Published execution remote moves before lease -> automatic path fails closed; no
  unconditional/weak force retry.
- Unsupported divergent product topology -> remains blocked; no guessed strategy.
- Omitted explicit target + product moves between admission and effect -> no
  transplant to an unbound newer HEAD.
- Same admitted request ID replay after product movement -> original bound target or
  durable completed result remains authoritative.
- Explicit matching `--onto` -> succeeds through the same service-owned
  classification/proof path.
- Explicit stale/mismatched `--onto` -> fails closed.
- Legacy explicit `update-base --rewrite-published`, if retained, proves no broader
  authority than the automatic path.
- Admitted request later rejected by the service -> client acknowledgement says
  request accepted/admitted, not reconciliation succeeded/resolved.
