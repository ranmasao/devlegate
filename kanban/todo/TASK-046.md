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
