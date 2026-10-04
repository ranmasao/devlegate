---
"type": "devlegate.ticket"
"title": "Fail closed when product remote is temporarily unobservable after worker completion"
---

## Milestone

Devlegate 0.5.6 recovery hardening.

## Goal

Do not misclassify a temporary inability to observe the product remote as product
movement/reconciliation after a worker has finished and its checkpoint is already
durable.

If the product remote cannot be observed because fetch/network access fails, retain
the completed worker checkpoint and execution binding and retry observation later.
Do not synthesize a reconciliation record with missing Git identities.

## Dogfood incident

TASK-044 execution:

```text
execution=2527642ee1794ed794721852c1dba2a9
code_base_head=fa6f3c23e2536d58cc316c961f71d01edbf78d76
workspace_head=6ab57e8afab31fa37377ba41ee2b628f88057789
```

The worker itself failed because OpenCode lost network access:

```text
OpenCode error: Cannot connect to API: Unable to connect.
```

Devlegate nevertheless checkpointed the workspace successfully:

```text
execution checkpoint created: 6ab57e8afab3
```

The product remote did not actually move. The canonical
`release/0.5.6` generation remained:

```text
fa6f3c23e2536d58cc316c961f71d01edbf78d76
```

and the execution branch was safely published at:

```text
devlegate/work/TASK-044 -> 6ab57e8afab31fa37377ba41ee2b628f88057789
```

However, immediately after checkpointing, `_observe_product_generation()` could not
successfully fetch the product remote. Its current contract returns:

```python
remote_head = ""
stable = False
reason = "product generation could not be observed"
```

The post-worker path treats every `stable=False` result as product drift and tries
to create reconciliation with:

```python
"product_remote_head": current_product_remote  # ""
```

But reconciliation state invariants require every mandatory identity, including
`product_remote_head`, to be a non-empty string. The state save therefore fails
with:

```text
invalid reconciliation identity
```

The next recovery attempt then reports:

```text
workflow blocked: product checkout or remote changed before execution recovery
```

This conflates two materially different states:

1. product generation is observed and changed;
2. product generation cannot currently be observed.

## Required behavior

Product observation must classify at least:

```text
stable
changed
unobservable
unsafe-local
```

or an equivalent typed/result model.

A failed fetch / unavailable remote MUST NOT be converted into a reconciliation
record.

When remote observation is temporarily unavailable after the worker checkpoint is
durable:

- keep the execution in a recoverable post-checkpoint state;
- keep the exact pending execution report;
- keep the exact worker checkpoint;
- do not clear or rewrite the execution binding;
- do not create reconciliation evidence;
- do not fabricate an empty or fallback remote identity;
- publish an operator-visible blocked reason such as:
  `product remote could not be observed; retrying when connectivity returns`;
- on a later successful observation:
  - if local+remote still equal the admitted product generation, continue normal
    publication/lifecycle;
  - if product genuinely moved, enter the existing reconciliation path using fully
    observed Git identities.

The same distinction must apply during restart/recovery of a persisted
post-checkpoint execution. A temporary fetch failure must remain retryable and must
not be reported as product movement.

## Safety constraints

- Do not weaken exact product-generation proof.
- Do not treat stale local remote-tracking refs as a successful fresh remote
  observation after fetch failure.
- Do not assume the remote stayed unchanged merely because it was unchanged before
  the outage.
- Do not enter reconciliation without a complete observed product identity.
- Do not discard the pending report/checkpoint while observation is unavailable.
- Do not automatically publish/integrate worker work until product stability is
  proven again.

## Acceptance criteria

- Network/fetch failure after checkpoint does not raise
  `invalid reconciliation identity`.
- No reconciliation object is persisted with missing/empty mandatory Git IDs.
- Execution remains durably recoverable at the exact checkpoint.
- Repeated observation failures are idempotent and preserve the same execution
  identity/report/checkpoint.
- Connectivity restoration with unchanged product resumes normal lifecycle exactly
  once.
- Connectivity restoration with genuinely changed product enters normal
  reconciliation with complete identities.
- Restart during the outage recovers the same post-checkpoint state.
- Operator diagnostics distinguish `remote unobservable` from `product changed`.
- Full tests, coverage, and Ruff remain green.

## Required regressions

1. Worker completes/fails, checkpoint is durable, product fetch fails:
   - no reconciliation record;
   - no invalid state;
   - exact execution/report/checkpoint retained.

2. Multiple consecutive fetch failures:
   - state remains identical/recoverable;
   - no duplicate evidence or lifecycle effects.

3. Restart while remote remains unavailable:
   - same execution binding is recovered;
   - still blocked as unobservable, not changed.

4. Remote returns and is unchanged:
   - normal execution publication/lifecycle resumes once.

5. Remote returns and has advanced:
   - existing reconciliation path is entered with complete observed identities.

6. Local product checkout changed while remote is unavailable:
   - fail closed as unsafe-local / ambiguous;
   - do not claim remote movement.

7. Fetch fails but a stale remote-tracking ref exists:
   - stale ref is not treated as a fresh successful observation.

## Non-goals

- Changing reconciliation policy for genuinely moved product generations.
- Retrying the external coding-agent API itself.
- Modifying TASK-044 forced-retry semantics.


### First review: classification direction is correct, but reconciliation compatibility is broken and required regressions are missing

Execution `306f8ac9fb7241058bcd4f8433d50971` / checkpoint
`d83339f2b1a56ef2f4118f96d26ef05fff9cb907` moves product observation in the right
direction by introducing explicit `stable`, `changed`, `unobservable`, and
`unsafe-local` classifications.

The important intended behavior is present conceptually:

- a failed fetch is not represented as an empty remote identity;
- post-checkpoint observation can report an operator-visible retryable
  `unobservable` state;
- changed product reconciliation now requires a non-empty observed remote identity.

However, this implementation cannot be accepted yet.

#### Blocker 1: existing reconciliation behavior is broadly broken

GitHub Actions run `37211969918` fails:

```text
42 failed, 1078 passed, 1 skipped
```

The failures are concentrated in existing reconciliation/update-base/recovery tests
and mostly fail with:

```text
KeyError: 'reconciliation'
```

Examples include:

- `test_engine_reconciliation_resume_reuses_retained_report_without_worker`;
- `test_resolving_reconciliation_restart_recovers_retained_report[*]`;
- `test_reconcile_resume_rejects_unsafe_state[*]`;
- update-base lineage/conflict/rewrite tests;
- dirty/wrong-branch/detached product reconciliation tests;
- product mutation lifecycle tests.

The regression is caused by changing the checkpoint recovery decision structure too
broadly.

Previously `_recover_checkpoint_publication()` only observed product generation for
the zero-delta special case:

```python
product = (
    self._observe_product_generation(report.code_base_head)
    if report.workspace_head == report.code_base_head
    else None
)
```

The new implementation observes/classifies product unconditionally and routes
`unsafe-local` directly to a blocked error before the established reconciliation
state can be persisted.

TASK-046 is specifically about distinguishing *remote unobservable* from *observed
product movement*. It must not redefine existing genuine product-drift,
dirty-product, wrong-branch, detached, update-base, or reconciliation behavior.

Preserve the old reconciliation policy. Introduce the new unobservable distinction
at the observation/recovery boundary without bypassing the existing reconciliation
creation paths for states that were previously reconcilable.

#### Blocker 2: no TASK-046 regression tests were added

The checkpoint changes only:

```text
src/devlegate/runtime.py
```

The ticket requires executable proof for all of the outage/recovery cases, not only a
production-code implementation.

Add at least the required matrix:

1. checkpoint durable + fetch failure:
   - no reconciliation;
   - no invalid reconciliation identity;
   - exact execution/report/checkpoint retained;

2. repeated fetch failures:
   - idempotent same state and execution identity;

3. restart during outage:
   - same post-checkpoint binding;
   - still classified unobservable;

4. connectivity restored, product unchanged:
   - normal publication/lifecycle resumes exactly once;

5. connectivity restored, product changed:
   - existing reconciliation path is entered with complete identities;

6. local product changed while remote unavailable:
   - fail closed as unsafe/ambiguous;
   - do not claim observed remote movement;

7. fetch failure with stale remote-tracking ref:
   - stale ref is not accepted as a fresh observation.

Also update existing test doubles that return product observation dictionaries so
they include the new classification contract where appropriate. For example:

```text
test_force_retry_rejects_product_movement_without_mutation
-> KeyError: 'classification'
```

That test failure is a compatibility issue introduced by the new observation
contract.

#### Blocker 3: typed observation must preserve existing callers

If `classification` becomes part of the observation contract, all internal callers
and test doubles need a consistent compatibility story. Avoid code that assumes every
legacy/mock dictionary has the new key unless the contract is deliberately migrated
everywhere in the same change.

A small typed dataclass/result object may be cleaner, but it is not required. The
important requirement is that the migration be complete and ordinary
reconciliation/force-retry behavior remain unchanged.

### Acceptance boundary

Return to review when:

```text
TASK-046 seven-case outage/recovery matrix: pass
existing reconciliation/update-base suite: pass
ordinary force-retry compatibility: pass
full coverage+xdist CI: green
Ruff: green
```

No new reconciliation policy is requested. This ticket should narrowly separate
`unobservable` from genuinely observed product state while preserving all existing
reconciliation semantics.
