---
"type": "devlegate.ticket"
"title": "Audit test-suite decomposition and focused proof sets"
"depends_on": ["TASK-036"]
---

## Milestone

Devlegate 0.5.6 focused validation research.

## Goal

Determine how far the current Devlegate test suite can be decomposed into smaller,
stable proof domains so an implementation agent can run the minimum meaningful
validation during its edit loop without routinely running large mixed files or the
full suite.

This is a research/audit ticket. It must produce evidence and a decomposition plan
before the QA workflow, validation-policy, or agent test-tool designs are treated as
implementation-ready.

## Context

The maintained `tests/TEST_BOUNDARIES.md` already distinguishes proof strength:

```text
engine semantic -> IPC/owner handoff -> production topology
```

That map answers what a test proves and which lower layer cannot replace a stronger
boundary. It does not yet answer a different operational question:

```text
given a small implementation change, what is the smallest trustworthy set of tests
that gives useful feedback before full CI?
```

The current suite is also physically concentrated in several large mixed modules.
At the start of this audit, notable examples include approximately:

```text
tests/test_control_plane.py   148 tests
tests/test_cli.py             131 tests
tests/test_daemon.py           63 tests
tests/test_ipc_server.py       61 tests
tests/test_worker_protocol.py  40 tests
```

Those files contain multiple distinct proof domains. File-level selection is
therefore often much broader than the change being developed.

Examples already visible from test names and the maintained boundary map include:

- control/scheduler semantics versus execution workspace/recovery/reconciliation;
- CLI parser/render/bootstrap behavior versus IPC client behavior versus
  `test_real_service_*` production topology;
- host/readiness/diagnostics versus graceful lifecycle/drain behavior;
- Unix socket transport/ownership versus mutable owner-thread handoff;
- worker egress parsing versus live process-group supervision.

The project must not optimize focused feedback by deleting necessary cross-layer
proof or pretending a cheap component test replaces a production-topology invariant.

## Research questions

Answer at least the following from the current suite and measured execution:

1. What semantic proof domains actually exist today at test/node level?
2. Which large files mix independent domains that can be selected or physically
   separated without weakening fixtures, provenance, or boundary meaning?
3. Which tests are expensive because of process topology, Git/worktree setup,
   subprocess startup, sleeps/timeouts, packaging/build work, or shared fixtures?
4. Which tests have high shared setup cost such that splitting their file would not
   materially reduce feedback time?
5. For common implementation-change classes, what is the smallest useful focused
   proof set?
6. Which invariants deliberately require two or more layers and therefore must not
   be collapsed into one "fast" profile?
7. Which current tests are misplaced, overly broad, or coupled through helpers in a
   way that prevents safe focused selection?
8. Is the best focused-selection unit a file, pytest node/group, marker, test class,
   named profile, physical module split, or a combination? Do not assume the answer
   in advance.
9. Which proposed decompositions are purely organizational, and which would require
   production/test-harness refactoring first?
10. What should an implementation worker normally run locally, and what should be
    left to authoritative full CI because its cost or topology makes it unsuitable
    for routine edit-loop use?

## Required investigation

### Inventory and classification

- Inventory the current collected tests at node/test level.
- Map them to semantic domains and the proof layers already defined by
  `tests/TEST_BOUNDARIES.md`.
- Preserve the distinction between component, engine semantic, IPC/owner, and
  production-topology proof.
- Identify mixed modules and mixed fixtures rather than classifying only by filename.
- Record where one invariant intentionally has evidence at several layers.

### Cost observation

- Measure representative runtime using the current supported development
  environment.
- Use pytest collection/timing facilities or equivalent repository-supported
  mechanisms; do not add a permanent runtime dependency merely for this audit.
- Record enough evidence to distinguish cheap component tests from materially
  expensive Git/worktree, process, LiveService, packaging, and release/distribution
  tests.
- Treat timing as observed evidence for this environment, not as a permanent
  performance guarantee.
- Identify setup/teardown costs and fixture coupling where they materially affect
  focused-run economics.

### Candidate focused domains

Evaluate at least these candidate change areas against the actual suite:

- CLI parsing/help/rendering;
- project/bootstrap/read-only commands;
- scheduler/control-plane semantics;
- execution workspace and publication;
- retry/reconciliation/recovery;
- lifecycle/stop/restart;
- worker egress/protocol;
- worker process supervision;
- IPC framing/socket ownership;
- IPC mutable owner handoff;
- systemd/host integration;
- status/snapshot presentation;
- packaging/distribution/licensing;
- full-source/release tooling.

The final taxonomy may merge, split, or rename these based on evidence.

### Decomposition proposals

For each useful candidate split, record:

- current tests/node IDs involved;
- current proof layer;
- shared helpers/fixtures;
- whether physical file movement is useful;
- whether a logical named subset is sufficient;
- expected feedback-time benefit;
- risks of accidentally omitting a required stronger-layer proof;
- any prerequisite harness refactor.

Pay special attention to `test_control_plane.py`, `test_cli.py`,
`test_daemon.py`, and `test_ipc_server.py`.

## Deliverable

Add a durable audit document, preferably
`tests/FOCUSED_TEST_AUDIT.md` unless the existing documentation structure makes a
different nearby location clearly better.

The document must contain:

- current inventory and observed cost summary;
- semantic/proof-domain map below the existing broad layer taxonomy;
- candidate decomposition of the large mixed modules;
- a proposed set of human-readable focused proof sets for common change classes;
- explicit "must also run" cross-layer relationships where a stronger proof is
  required;
- tests or domains that should remain full-CI-only during ordinary agent edit loops;
- recommended follow-up implementation/refactoring tickets in dependency order;
- unresolved questions where evidence is insufficient.

Update `tests/TEST_BOUNDARIES.md` only where the audit discovers that its current
classification is inaccurate or where a concise cross-reference to the focused audit
is useful. Do not duplicate the whole document.

## Scope boundaries

- Do not implement QA workflow or QA state.
- Do not define project validation policy.
- Do not define the final agent test-tool protocol.
- Do not introduce a generic workflow/gate policy.
- Do not bulk move/rename test files merely to demonstrate the proposed layout.
- Do not remove tests as "redundant" solely because a similar scenario exists at
  another proof layer.
- Do not change production behavior.
- Small audit-only scripts are allowed only if they materially improve reproducible
  measurement and are clearly test/development tooling; prefer existing pytest
  facilities where sufficient.
- Full CI remains the authoritative repository-wide proof regardless of focused-set
  recommendations.

## Acceptance criteria

- Every major current test family is assigned to a meaningful semantic/proof domain
  or explicitly marked cross-domain.
- The four largest mixed modules have evidence-based decomposition proposals.
- Representative runtime/cost evidence is recorded for cheap and expensive proof
  families.
- At least one concrete minimal focused set is proposed for each investigated common
  change area where the suite supports one.
- Cross-layer invariants that require stronger production evidence are explicitly
  protected from accidental "fast-test replacement".
- The audit identifies where physical file decomposition would actually reduce
  worker feedback cost versus merely reorganize source.
- The audit is sufficient to revise TASK-025/TASK-026/TASK-029/TASK-030 later
  without guessing about the current suite.
- No QA/policy implementation or production behavior is introduced.
- Existing tests and Ruff remain green after documentation/audit changes.

## Required regressions

- Existing `TEST_BOUNDARIES.md` proof-strength rules are preserved unless the audit
  provides concrete contradictory evidence.
- No `test_real_service_*` production-topology proof is reclassified as replaceable
  by a direct engine/component test without equivalent process-boundary evidence.
- No test is deleted or disabled merely to improve measured focused-run time.


## Accepted

Execution `9d53875c385349eeb5d736f1675ccc76` / checkpoint
`272c592d7746d404bc35107cf45757e2c9ddf3f4` satisfies the focused-test
audit requirements.

The audit is evidence-based and remains research-only:

- 1,082 collected tests are inventoried across the current 39 test modules;
- representative timings distinguish cheap component selections from expensive
  Git/worktree, socket, worker, lifecycle, and production-topology families;
- the existing component -> engine semantic -> IPC/owner handoff -> production
  topology proof hierarchy is preserved;
- all requested operational change areas have a concrete minimum focused proof set
  plus explicit stronger-layer companions;
- the four largest mixed modules (`test_control_plane.py`, `test_cli.py`,
  `test_daemon.py`, `test_ipc_server.py`) have evidence-based decomposition
  proposals, fixture-cost analysis, and prerequisites where physical splitting
  would otherwise be cosmetic;
- `test_real_service_*` and other production-topology evidence are explicitly
  protected from replacement by cheaper direct/component tests;
- full-CI-only / normally deferred domains are identified without weakening full CI
  authority;
- node IDs / reviewed node groups are recommended as the current focused-selection
  unit, with named profiles and physical splitting deferred until later measured
  implementation work;
- follow-up work is ordered around inventory, fixture extraction, IPC/daemon splits,
  named profiles, and only then optional physical reorganization;
- unresolved runtime and fixture-economics questions are recorded rather than
  guessed.

The product diff contains only `tests/FOCUSED_TEST_AUDIT.md`; no production,
workflow, QA-state, validation-policy, or test behavior changed.

GitHub CI run `36910733298` is green:

```text
1081 passed, 1 skipped
coverage: 79%
Ruff: all checks passed
```

No further pre-QA audit changes are required.
