---
"type": "devlegate.ticket"
"title": "Decompose mixed tests into semantic domains and shared fixtures"
"depends_on": ["TASK-038"]
---

## Milestone

Devlegate 0.5.6 test-suite decomposition and focused validation preparation.

## Goal

Restructure the current mixed test suite into clear semantic test domains, and
extract shared fixtures/helpers where doing so reduces coupling or focused-run cost,
without weakening proof boundaries or changing production behavior.

The result should make it obvious to both humans and implementation agents where a
new regression belongs and should make domain-scoped test execution practical
without requiring the full suite during every edit loop.

## Context

TASK-038 audited the current suite and established that the largest test modules mix
multiple semantic proof domains:

- `tests/test_control_plane.py`;
- `tests/test_cli.py`;
- `tests/test_daemon.py`;
- `tests/test_ipc_server.py`.

The audit also established that whole-file selection is often a poor worker
edit-loop unit, while explicit semantic node groups are already useful. It
recommended physical decomposition only where it improves source ownership,
fixture boundaries, or focused-run economics, and warned against treating cheaper
component tests as substitutes for stronger IPC/owner or production-topology proof.

`tests/TEST_BOUNDARIES.md` remains the authoritative proof-strength map.
`tests/FOCUSED_TEST_AUDIT.md` is the evidence base for the domain taxonomy and
runtime-cost observations.

This ticket is intentionally about test organization and fixture structure. It does
not yet define tracked validation policy, named execution profiles, QA workflow, or
the agent test-tool protocol.

## Required behavior

### Semantic decomposition

- Start from the domains identified by `tests/FOCUSED_TEST_AUDIT.md`, then verify
  them against the current suite before moving tests.
- Split mixed modules by semantic ownership rather than by arbitrary file size,
  source-line range, or implementation module name.
- At minimum, reassess and decompose the four large mixed modules identified by
  TASK-038:
  - control-plane/scheduler/workspace/publication/retry/reconciliation/recovery;
  - CLI syntax/bootstrap/IPC/lifecycle/production-topology;
  - daemon host/readiness/lifecycle/process-loss or recovery concerns;
  - IPC dispatch/transport/owner-handoff/recovery concerns.
- Exact final filenames are an implementation decision, but names must communicate
  the semantic domain clearly.
- Keep genuinely cross-domain or stronger-boundary tests in a location whose name
  reflects their actual proof role rather than forcing every test into an
  artificially narrow category.

### Fixture and helper decomposition

- Identify shared fixtures/helpers that currently force unrelated test domains to
  construct expensive Git, service, socket, worker, or process topology.
- Extract or split fixtures when that materially improves domain isolation,
  readability, or focused-run cost.
- Prefer small explicit fixture layers over one universal fixture that silently
  provisions more topology than the test requires.
- Do not duplicate complex Git/service setup across files merely to complete the
  physical split.
- Preserve deterministic teardown and ownership rules for worktrees, sockets,
  subprocesses, worker groups, temporary state, and other test resources.
- Do not introduce broad production refactoring solely to make test files prettier.
  If a useful test split requires non-trivial production redesign, document that as
  follow-up rather than expanding this ticket without bound.

### Proof-boundary preservation

- Preserve the proof hierarchy documented in `tests/TEST_BOUNDARIES.md`:
  component -> engine semantic -> IPC/owner handoff -> production topology.
- A moved test must continue to exercise the same effective boundary and assertions.
- Do not replace `test_real_service_*` or other production-topology coverage with
  cheaper direct-engine or in-process tests.
- Do not delete or weaken cross-layer companion tests merely because a lower-layer
  domain now has a cleaner home.
- Keep full CI as the authoritative repository-wide proof.

### Placement guidance for future tests

Add short maintained guidance under `tests/` telling future developers and agents
where new regressions belong.

The guidance should be concise and operational. It should include at least:

- choose the narrowest semantic domain that proves the behavior;
- do not choose a test file merely because it imports the implementation being
  changed;
- state where the major domains live;
- explain that a cheaper proof layer does not replace a required stronger layer;
- point to `TEST_BOUNDARIES.md` for proof strength and
  `FOCUSED_TEST_AUDIT.md` for audit rationale.

Each new major semantic test module should also carry a short module docstring or
equivalent nearby comment describing:

- what the module owns;
- important concerns it intentionally does not own;
- its proof layer when that is not obvious;
- any stronger companion boundary that commonly applies.

Keep these comments short enough that agents are likely to read and follow them.

### Focused-run usability

- Demonstrate that the resulting domains can be selected independently using normal
  repository-supported pytest invocation.
- Record representative focused-run commands for the major domains in the placement
  guidance or nearby test documentation.
- Do not introduce final machine-readable validation-profile IDs in this ticket;
  TASK-026 will later map stable project policy onto the resulting real domain
  structure.
- Do not add a generic `fast`/`slow` taxonomy. Selection is semantic.

## Scope boundaries

- No QA workflow or `kanban/qa` implementation.
- No tracked validation-policy schema or profile-generation mechanism.
- No `devlegate_test` worker tool.
- No worker prompt/profile guidance beyond the test-placement documentation needed
  here.
- No coverage-percentage target in this ticket.
- No production behavior changes unless a tiny test-harness seam is strictly
  necessary and independently justified.
- No mass renaming solely for aesthetic consistency.
- No test deletion merely because another layer covers a similar scenario.

## Acceptance criteria

- The current large mixed test modules are decomposed where TASK-038 found distinct
  semantic domains, or the implementation documents concrete fixture/proof reasons
  for leaving a remaining mixed group intact.
- New module boundaries correspond to actual semantic proof domains rather than
  arbitrary file-size splits.
- Shared fixtures/helpers are extracted or narrowed where that produces real domain
  isolation or avoids unnecessary expensive setup.
- Resource ownership and teardown remain deterministic.
- Existing test semantics and proof strength remain intact.
- Future agents have concise, discoverable placement guidance and each major new
  domain module identifies its ownership boundary.
- Major semantic domains can be run independently without invoking unrelated
  production-topology, packaging, or lifecycle families by accident.
- The resulting organization is suitable input for TASK-026 named validation
  profiles without requiring TASK-026 to rediscover the taxonomy.
- Full tests, coverage collection, and Ruff remain green.

## Required regressions / verification

- Collection count is preserved except for deliberate parametrization/helper
  changes that are explicitly explained; no test silently disappears during moves.
- Representative scheduler/control-domain selection runs independently.
- Representative workspace/publication or reconciliation/recovery selection runs
  independently.
- CLI parser/bootstrap selection does not implicitly run real-service topology
  tests.
- IPC transport selection does not implicitly require mutable owner-handoff proof
  unless the selected behavior actually needs it.
- Owner-handoff tests still exercise the real owner-thread boundary.
- Production-topology tests still start the independent service/CLI process path.
- Existing cross-layer recovery/lifecycle invariants remain present after the split.
- Running the full suite after reorganization produces the same behavioral result
  class as before the move.
