---
"type": "devlegate.ticket"
"title": "Define project validation domains and execution profiles"
"depends_on": ["TASK-038"]
---

## Milestone

Next-iteration validation pipeline.

## Goal

Define project-owned validation policy that names deterministic QA validators and
agent-focused test profiles without hard-coding pytest, Ruff, or Devlegate's own
test layout into the runtime.

Shared validation meaning must travel with the project; machine-specific toolchain
locations and operational limits must remain local.

## Context

Devlegate currently has a rich human-readable `tests/TEST_BOUNDARIES.md`, and the
worker contract says to run focused validation, but there is no machine-readable
answer to:

- which validation domains exist;
- which files/changes make a deterministic validator applicable;
- which focused test profile corresponds to a class of work;
- how a project-specific command is executed safely;
- which settings are shared project policy versus local machine/toolchain choices.

Different projects may use pytest, CTest, Cargo, Go, npm, Meson, or other tooling.
Devlegate runtime must remain stdlib-only.

TASK-038 defines where a deterministic validation gate exists in a project's workflow;
this ticket defines what project-owned validators and agent test profiles mean once
such capabilities are selected.

This work should compose with the future tracked-policy/local-configuration split in
TASK-014 without requiring that larger migration to land first.

## Required behavior

- Define a versioned project-owned validation catalog under the tracked
  `.devlegate/` integration boundary or an equivalent tracked project-policy
  location.
- The catalog exposes stable profile/domain identifiers rather than embedding
  Devlegate-specific assumptions in runtime code.
- Support at least two semantic uses:
  - **QA validators**: deterministic checks Devlegate may run automatically;
  - **agent test profiles**: focused tests an implementation worker may invoke while
    developing.
- QA applicability can be resolved deterministically from the exact product delta
  and declared path/domain policy.
- Profile commands are represented as argv/data, not arbitrary shell fragments
  interpreted by Devlegate.
- Commands execute against the exact execution workspace/checkpoint being validated.
- The policy must support project-specific runners without requiring Devlegate to
  depend on pytest, Ruff, Node, Cargo, or any other project tool.
- Shared profile meaning, path/domain membership, and command shape are tracked
  project policy.
- Machine-specific executable/toolchain locations, resource ceilings, and similar
  installation choices are local configuration and must not be committed merely to
  make validation work on one host.
- Environment/secret overrides remain explicit; validator policy must not become a
  secret store.
- Policy resolution is deterministic and has a generation/fingerprint that can be
  bound to QA evidence and agent test runs.
- Conflicting or malformed validation policy fails closed with precise diagnostics.
- A project's full test suite may be represented as a profile, but it is not
  implicitly an automatic QA validator merely because it exists.
- Devlegate's own catalog should define useful domains/profiles corresponding to its
  current suite boundaries without discarding the explanatory role of
  `TEST_BOUNDARIES.md`.

## Acceptance criteria

- Devlegate can load and validate a tracked catalog containing multiple QA
  validators and focused test profiles.
- Two clones share profile/domain semantics while using different local toolchain
  bindings where required.
- The runtime can determine the applicable QA validator set from an exact delta
  without guessing from ticket prose.
- A worker-facing consumer can list stable focused-test profile IDs and descriptions.
- Commands execute as argv without a shell-expansion authority boundary.
- Invalid profile references, ambiguous local bindings, unsafe workspace escape, and
  malformed policy fail before process execution.
- Devlegate runtime gains no new Python/runtime dependency.
- Existing `TEST_BOUNDARIES.md` remains an architectural proof map; the new catalog
  answers what to run rather than replacing why each proof layer matters.
- Full tests and lint remain green.

## Required regressions

- A source-path delta selects the expected QA domain deterministically.
- A delta outside all QA domains yields an empty applicable validator set.
- A named focused-test profile resolves to the same shared definition in two clones.
- Different local toolchain paths can execute the same shared profile without
  changing tracked policy.
- A profile cannot escape the execution workspace through its working-directory
  policy.
- Shell metacharacters in data/selector fields are passed as data, not evaluated by
  a shell.
- A validation-policy generation change produces a different identity for subsequent
  runs and cannot silently rewrite evidence already bound to an older generation.
