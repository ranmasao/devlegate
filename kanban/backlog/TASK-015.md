---
"type": "devlegate.ticket"
"title": "Define a declarative version-schema protocol"
---

## Milestone

Post-0.6 release/version modeling.

## Goal

Specify a small deterministic schema language that describes the syntax and
canonical structure of version identifiers used by a project, so a human can give
examples and semantics to an agent and the agent can author a validated project
version-schema file.

## Context

Projects may use SemVer-like identifiers, calendar versions, ordinal versions,
hybrids, or project-specific combinations. Devlegate should not require a growing
hard-coded list of named version schemes.

This ticket concerns the shape/grammar of a version identifier only. It does not
decide when to release, which version comes next, or which bump is appropriate.

A normative document such as `docs/VERSION_SCHEMA.md` and a project file such as
`.devlegate/version-schema.yml` are candidate forms; exact naming should be
decided during design.

## Required behavior

- Define a versioned meta-schema for describing canonical version identifiers.
- Support a deliberately small set of composable primitives sufficient for common
  semantic, calendar, ordinal, prerelease, and hybrid forms.
- Named components must be available to later branch/tag policy without requiring
  reparsing by agents.
- Parsing and rendering must be deterministic and canonical; equivalent ambiguous
  textual representations should not silently normalize to the same state unless
  explicitly designed.
- Allow positive and negative examples as validation fixtures in or alongside the
  schema.
- Treat user-provided examples as evidence, not authority for unstated semantic
  names. Agents must not infer `major`, `year`, or other meanings that were not
  provided or safely derivable.
- Keep bump policy, release authority, branch effects, and publication semantics out
  of VersionSchema.

## Acceptance criteria

- The normative specification is sufficient for an agent to author a project schema
  from human instructions and examples without undocumented conventions.
- Devlegate can parse, validate, render, and extract named components from a valid
  schema deterministically.
- Common SemVer-like and CalVer-like examples can be represented without special
  hard-coded parser branches for each named scheme.
- Invalid schema documents and non-canonical version strings fail with precise
  errors.
- Schema evolution has an explicit version/compatibility mechanism.

## Required regressions

- For every accepted canonical example, parse/render round trips exactly.
- Declared invalid examples are rejected.
- Width/range/enum/optional-fragment constraints behave identically across runs.
- VersionSchema tests do not depend on release/bump decision logic.
