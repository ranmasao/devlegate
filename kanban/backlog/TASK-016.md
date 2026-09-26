---
"type": "devlegate.ticket"
"title": "Model project branch and release topology as policy"
---

## Milestone

Post-0.6 branch/release policy.

## Goal

Define project-owned declarative policy for mapping product/version information to
development lines, release branches, integration targets, and tag names without
hard-coding Devlegate's own workflow into the engine.

## Context

Projects differ substantially:

- some, such as simple research projects, may work continuously on `master`;
- Devlegate develops on versioned branches and later integrates those lines into
  `master`;
- other projects may use major-only release branches, calendar branches, stable/LTS
  lines, or no release branches at all.

Version identifiers and branch topology are related but distinct. Branch policy may
consume named components exposed by VersionSchema, while fixed-branch projects may
ignore versions entirely.

This ticket describes topology/rules. It must not decide that a release should
happen.

## Required behavior

- Represent fixed product/development branches as a first-class simple case.
- Represent version-derived branch/tag naming through explicit templates or
  equivalent deterministic rules over validated named components.
- Represent release integration targets where a project uses them.
- Keep local Git remote aliases separate from canonical branch naming.
- Do not assume SemVer, `release/<major>.<minor>`, `main`, or `master`.
- Fail closed when required version components or topology inputs are unavailable or
  ambiguous.
- Make the resolved topology available to execution provenance and role rendering so
  all authorities reason about the same branch generation.
- Keep release timing/authorization and version-bump choice out of this policy.

## Acceptance criteria

- A fixed-master project can be represented without fake version/release concepts.
- Devlegate's versioned release-line workflow can be represented declaratively.
- At least one calendar/hybrid-style branch naming example can be represented from
  named version components.
- Runtime can resolve canonical refs deterministically from policy and exact input
  state.
- Policy changes have an explicit generation/provenance story and do not silently
  retarget an already-admitted execution.

## Required regressions

- Fixed-branch and version-derived examples resolve deterministically.
- Missing or malformed component references fail before Git mutation.
- An execution admitted under one resolved topology cannot be silently finalized
  against a different topology generation.
