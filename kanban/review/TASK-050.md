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
