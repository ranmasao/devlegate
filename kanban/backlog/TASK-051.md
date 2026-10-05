---
"type": "devlegate.ticket"
"title": "Add resolved project inspection UX"
"depends_on": ["TASK-014"]
---

## Milestone

Project introspection UX.

## Goal

Provide a human- and machine-readable command analogous in spirit to `docker
inspect` that exposes Devlegate's fully resolved project model, including active
configuration documents, local/runtime bindings, workflow/capability references,
and provenance.

## Context

As project configuration, workflow definitions, and optional capabilities grow,
operators and agents need one authoritative way to answer:

- what project does Devlegate think this is?
- which tracked policy documents are active?
- which local settings are bound on this machine?
- where did each class of resolved properties come from?
- which workflow/capabilities are enabled?
- what exact policy/configuration generation is currently in force?

TASK-014 must first establish the layered configuration and source/provenance model.

## Required behavior

- Add a project inspection command or equivalent primary UX surface.
- Provide concise human-readable output and a stable structured output mode such as
  JSON.
- Report resolved values together with ownership/source information where useful:
  tracked project policy, machine-local configuration, explicit environment
  override, built-in/default resource, or other supported source class.
- Include the active project-policy documents/files as first-class project
  properties.
- Expose exact configuration/policy generation identities useful for provenance and
  debugging.
- Do not expose secret values; indicate presence/source without leaking sensitive
  content.
- Keep inspection read-only and safe when the runtime service is absent.
- Do not duplicate configuration-resolution logic in the CLI; inspect the canonical
  resolved model produced by the configuration layer.

## Scope boundaries

- This is UX/introspection, not the configuration redesign itself.
- Do not add arbitrary mutation/edit commands as part of inspection.
- Do not require optional testing/versioning/release capabilities to exist.
- Do not expose secrets merely because they participate in resolution.

## Acceptance criteria

- A user can inspect a configured project and see its effective project identity,
  active tracked documents, local runtime bindings, enabled workflow/capability
  references, and source/provenance classes.
- Structured output contains the same authoritative resolved model without
  presentation-only ambiguity.
- Missing optional capabilities are represented clearly without errors.
- Secret-bearing inputs are redacted.
- Inspection remains correct across two clones that share tracked policy but use
  different local runtime settings.
