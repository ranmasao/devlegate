---
"type": "devlegate.ticket"
"title": "Audit remaining test modules for semantic ownership and fixture boundaries"
"depends_on": ["TASK-049"]
---

## Milestone

Test architecture continuation.

## Goal

Perform a second decomposition pass over the test suite after TASK-049, covering the
remaining test modules that were not part of the four largest mixed files. Split
only where semantic ownership or fixture boundaries justify it.

## Context

TASK-049 established the current placement rule:

- semantic test modules own regressions;
- support modules own shared fixtures/helpers;
- tests belong to the narrowest semantic owner that proves the behavior;
- proof strength remains separate from physical/domain ownership.

The first pass focused on control-plane, CLI, daemon, and IPC-server monoliths.
Remaining modules may already be cohesive. This ticket must not split files merely
to make them smaller.

The suite and domain taxonomy evolve bottom-up as new behavior and regressions
appear. A new semantic domain is valid when the current taxonomy no longer describes
an independently useful invariant family; it must not be invented speculatively.

## Required behavior

- Review all remaining maintained test modules against `TEST_PLACEMENT.md`,
  `TEST_BOUNDARIES.md`, and the current physical fixture/support structure.
- Pay particular attention to:
  - worker protocol / worker supervision;
  - execution-result and execution-workspace families;
  - project registry/context/runtime identity;
  - runtime store;
  - status/snapshot/output;
  - host installation/systemd;
  - packaging/distribution/standalone/full-source/licensing.
- For each non-trivial module, determine whether it is:
  - a cohesive semantic domain and should remain unchanged;
  - mixed and should be split;
  - semantically cohesive but coupled to an unnecessarily broad fixture;
  - support/helper code mixed with regressions and should be separated;
  - deliberately cross-layer and should stay explicitly cross-layer.
- Extract or narrow fixtures/helpers only where that improves semantic isolation,
  focused-run cost, or ownership clarity.
- If a genuinely new test domain is discovered, add it to the maintained placement
  guidance with a short ownership description.
- Preserve the existing proof hierarchy and stronger companion evidence.
- Do not create generic `fast`/`slow` groupings.
- Do not introduce machine-readable validation profiles yet.

## Acceptance criteria

- Every remaining test module has been reviewed against semantic ownership and
  fixture boundaries.
- Mixed modules found by the audit are decomposed, or the report gives a concrete
  reason for keeping them intact.
- New domains, if any, are explicit and documented rather than inferred from naming
  heuristics.
- Support code does not masquerade as semantic test ownership.
- Focused execution remains practical for each domain touched by the change.
- Collection count is preserved except for explicitly explained equivalent
  parametrization changes.
- Full tests, coverage collection, and Ruff remain green.

## Required regressions / evidence

- Record before/after module inventory and collection count.
- Demonstrate representative focused commands for every newly split domain.
- Show that fixture extraction does not accidentally pull unrelated service,
  process, socket, Git, or packaging topology into a narrow domain.
- Preserve production-topology and other stronger-boundary tests where required.


### Review: CI is green, but semantic ownership is still implemented as facades

Execution `79e17f40c6704ec292621b5ed6636099` / checkpoint
`3e3701a3352895c67bf7e8e5310c6d9cb740ff63` is mechanically healthy in CI:

```text
1130 passed, 1 skipped
coverage: 79%
Ruff: all checks passed
```

The worker-local incomplete result was environmental; authoritative CI proves the
published checkpoint. The remaining blocker is architectural.

#### The new domains do not physically own their tests

The new files:

- `test_worker_egress.py`
- `test_worker_process.py`
- `test_worker_logging.py`
- `test_project_lifecycle.py`

are collection facades. They import `test_worker_protocol` or
`test_project_registry` and populate their namespace with
`globals().update(...)`. The real test bodies remain in the source module after
being renamed from `test_*` to `_test_*`.

That recreates the indirection deliberately removed by TASK-049. It also contradicts
the maintained rule that semantic test modules own regressions and support modules
own shared fixtures/helpers.

The explicit-name mapping is safer than the first TASK-049 substring classifier, but
it still leaves semantic ownership split between two files: the advertised domain
file owns collection while the old mixed module owns implementation.

#### Required correction

Use the same ownership model accepted in TASK-049:

- Move the worker egress/process/logging test bodies physically into their semantic
  `test_worker_*.py` modules.
- Move project lifecycle test bodies physically into
  `test_project_lifecycle.py`.
- Extract reusable shared helpers/fixtures from `test_worker_protocol.py` and
  `test_project_registry.py` into explicit support modules (for example
  `_worker_support.py` / `_project_support.py`) when multiple semantic modules
  need them.
- Keep genuinely cohesive remaining regressions in
  `test_worker_protocol.py` / `test_project_registry.py` only if those files
  remain real semantic test domains. Do not retain hidden `_test_*` inventories.
- Remove facade collection via `globals().update(...)`.
- Update `TEST_PLACEMENT.md` / audit text so it describes physical ownership rather
  than “semantic collection facades”.
- Preserve the existing collection count and proof boundaries.

The rest of the second-pass audit can remain: the documented decision to leave
already cohesive modules intact is appropriate and this ticket should not split
files merely for size.

Return to review when:

```text
new semantic domain modules physically own their regressions
shared helpers have explicit support ownership
no hidden _test_* inventory / facade re-export is required
1130 passed, 1 skipped (or explicitly justified equivalent count)
coverage collection green
Ruff green
```


### Review: second attempt is still incomplete

Execution `933efc53ca8b448ab6ffdf4afef7ccb0` / checkpoint
`a396728b12c1669d49571d1138c8704d7a20de36` improved the split and CI is healthy:

```text
1130 passed, 1 skipped
coverage: 79%
Ruff: all checks passed
```

However the worker correctly reported `incomplete`, and the checkpoint still does
not satisfy the previous review requirement. In particular, project lifecycle tests
still delegate to private `test_project_registry._test_*` bodies, and worker
logging/private source ownership remains unfinished.

Complete the remaining items already listed in the execution report:

- physically move worker logging test bodies into `test_worker_logging.py`;
- physically move project lifecycle test bodies into `test_project_lifecycle.py`;
- remove the corresponding private moved-test bodies from the original modules;
- use the new support modules for genuinely shared setup/helpers only;
- remove any remaining delegation/hidden moved-test inventory;
- rerun full tests, coverage, and Ruff.

Do not add another wrapper/facade layer. Return to review only when the semantic
domain modules physically own the moved regressions.


### Review: physical ownership is closer, but two blockers remain

Execution `b8fb584ca1f546d18790853f57f25178` / checkpoint
`e0a823df69165add6859560681fe1a7d631c9cd7` made the requested physical moves for
worker logging and project lifecycle, but it is not ready for acceptance.

Authoritative CI proves the test/coverage side is healthy:

```text
1130 passed, 1 skipped
coverage: 79%
```

However CI fails Ruff with 12 errors, primarily unused/import-order residue created
by the moves. That alone blocks acceptance.

There is also still hidden moved-test inventory in
`tests/test_worker_protocol.py`. The file retains many private definitions such as:

```text
_test_worker_process_group_isolated_and_interrupts_descendant
_test_natural_leader_exit_does_not_prove_group_retirement
_test_worker_prompt_is_delivered_over_stdin_without_argv_pollution
_test_malformed_worker_events_fail_closed
_test_process_zero_and_malformed_json_preserve_independent_status
_test_duplicate_reports_fail_closed
...
```

These correspond to regressions already physically owned by
`test_worker_process.py` and `test_worker_egress.py`. The previous review
explicitly required removal of any remaining hidden moved-test inventory, not only
logging/lifecycle delegation.

#### Required correction

- Remove the duplicated/private moved egress/process test bodies from
  `test_worker_protocol.py`.
- Keep only genuinely remaining cohesive worker-protocol regressions there.
- Keep shared setup only in `_worker_support.py` where multiple semantic modules
  need it.
- Clean the stale/unused imports and import ordering caused by the moves.
- Re-run authoritative CI until tests, coverage collection, and Ruff all pass.

Return to review when:

```text
no globals/facade delegation
no hidden _test_* copies of moved regressions
1130 passed, 1 skipped (or explicitly justified equivalent)
coverage collection green
Ruff green
```
