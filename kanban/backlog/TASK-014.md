---
"type": "devlegate.ticket"
"title": "Separate tracked project policy from local runtime configuration"
---

## Milestone

Post-0.6 project configuration model.

## Goal

Replace the current project-local `.env` catch-all with a clear split between
version-controlled project policy, machine-local runtime configuration, and
environment/secret overrides.

## Context

The current `.env` mixes project semantics such as product/control branches and
workflow paths with machine-specific choices such as harness/model selection,
remote alias, polling, and state location.

Project-owned topology should be inherited consistently by all roles, executions,
and clones. Machine-specific harness/model settings and secrets should not need to
be committed to a public repository.

Different projects may use very different branch and release structures, so the
tracked configuration must not assume Devlegate's own versioned-branch workflow.

## Required behavior

- Define a version-controlled project-policy document under `.devlegate/` for
  durable project semantics.
- Define a machine-local configuration/profile mechanism for settings that belong to
  a particular installation or clone.
- Preserve environment variables as an explicit override/secret injection layer
  rather than the canonical project-policy store.
- Classify existing configuration fields by ownership instead of mechanically
  copying the old `.env` into a new format.
- Project branch/workflow topology must have one canonical source and be available
  consistently to runtime and rendered role protocols.
- Local Git remote aliases remain local unless a stronger project-level requirement
  is established.
- Migration/bootstrap behavior must be deterministic and fail closed on conflicting
  sources rather than silently picking one.

## Acceptance criteria

- The configuration precedence/ownership model is documented and mechanically
  testable.
- A fresh clone can obtain tracked project policy from the repository without
  copying another developer's model/harness/secrets.
- Two clones of the same project can use different local remotes/models/harness
  details while sharing the same project topology.
- Existing project registration/runtime identity semantics remain coherent.
- The old `.env` path has a deliberate compatibility/migration story rather than
  indefinite ambiguous coexistence.

## Required regressions

- Conflicting tracked and local authority for the same project-policy field is
  rejected according to the documented migration rules.
- Local secrets/model selections do not become generated tracked project files.
- Runtime, architect, reviewer, and future specialized roles resolve the same
  project-policy generation for one execution.
