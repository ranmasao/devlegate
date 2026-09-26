---
"type": "devlegate.ticket"
"title": "Isolate the OpenCode adapter from worker supervision"
---

## Milestone

Post-0.6 architecture cleanup.

## Goal

Separate OpenCode-specific command, configuration, event, and tool-adapter behavior
from generic worker process supervision, and ship the Devlegate reporting tool as a
static packaged resource instead of an embedded Python string.

## Context

WorkerSupervisor currently owns both generic OS process lifecycle concerns and
OpenCode-specific protocol/configuration details. It also generates
`devlegate_report.ts` inline from Python for every execution.

This is acceptable for 0.6, but it makes the runtime boundary harder to reason about
and obscures the fact that the reporting tool is an OpenCode adapter artifact rather
than Python business logic.

The work is intentionally deferred until after 0.6.

## Required behavior

- Generic worker supervision remains responsible for spawn/termination, process
  identity, signals, drain, logging, and transport ownership.
- OpenCode-specific command construction, environment/config preparation, JSON event
  interpretation, and tool materialization move behind an explicit adapter boundary.
- The Devlegate reporting tool is stored as a normal packaged static resource and
  materialized into the temporary OpenCode config directory at execution time.
- Core worker semantic validation remains owned by Devlegate rather than trusting
  OpenCode-side schema validation alone.
- Do not introduce Node/npm/TypeScript tooling as a Devlegate runtime dependency
  merely because OpenCode consumes a JS/TS tool artifact.
- Avoid designing a generalized multi-agent plugin framework unless concrete
  requirements justify it.

## Acceptance criteria

- WorkerSupervisor no longer contains embedded OpenCode tool source or OpenCode
  protocol-specific construction beyond the connector seam.
- The packaged reporting tool is present in all supported distribution forms and is
  materialized deterministically for OpenCode executions.
- Existing worker egress semantics and trust boundaries remain unchanged.
- Devlegate runtime remains stdlib-only.
- Tests prove packaging/materialization and connector behavior without requiring a
  separate Node toolchain in the normal Python test path.

## Required regressions

- Given an installed/package-resource environment, preparing an OpenCode execution
  materializes the exact packaged reporting tool.
- Given malformed or duplicate OpenCode reporting events, Devlegate still rejects
  them through its own worker-egress validation.
- Given worker termination/drain/recovery paths, extracting the OpenCode adapter does
  not change process ownership or cleanup behavior.
