# Changelog

This file records notable product changes by release. Entries are ordered from
the current release back to the first working release. Each entry describes
behavior shipped by that release; implementation steps superseded before a
release are not separate product changes.

## 0.5.5.dev0 -- unreleased

Automatic retry safety for product drift during zero-delta execution.

### Added

- Added a narrowly scoped automatic retry when a worker leaves its execution
  checkpoint unchanged while the product advances by one verified descendant
  generation during that execution.
- Preserved the original execution report and provenance, retired the stale
  zero-delta workspace, and admitted the fresh execution under a new durable
  execution identity without creating reconciliation state.

### Fixed

- Kept no-drift, divergent-history, unpublished-worker, and ticket-state races
  on the existing fail-closed or reconciliation paths.

## 0.5.4 -- 2026-09-26

Operational project hosting, durable lifecycle compatibility, and release-proof tooling.

### Added

- Added explicit service and execution log access, registered project addressing, and per-user host installation.
- Added systemd user supervision with automatic project hosting and standalone/Debian distribution proof tooling; binary publication remains deferred to 0.6.0.

### Changed

- Made service relaunch identity distribution-neutral and systemd unit authority compact, while retaining compatibility with legacy persisted units.
- Hardened reproducible standalone and Debian packaging, including direct installation of the Debian executable at `/usr/bin/devlegate`.

### Fixed

- Fixed lifecycle/systemd compatibility and error handling so managed stop and restart use authoritative units and report operational failures without tracebacks.

## 0.5.3 -- 2026-09-23

Checkpoint-aware service lifecycle, explicit execution retirement, and distribution hardening.

### Added

- Added checkpoint-aware `devlegate restart` for self-managed services, preserving service authority through graceful worker completion and replacement startup.
- Added `devlegate drop` to explicitly retire blocked executions without applying them while preserving worker reports and checkpoint provenance.

### Changed

- Made graceful service stop and restart respect worker checkpoint boundaries and durable lifecycle ownership before relinquishing service authority.
- Hardened licensing and package-distribution boundaries for Devlegate core, copyable default templates, and the vendored NanoYAML dependency.

### Fixed

- Fixed lifecycle response and wakeup ordering so completed lifecycle state cannot regress and accepted stop or restart requests cannot be delayed by a lost wakeup until the next polling interval.
- Hardened execution-drop recovery and cleanup, including submodule worktrees and restart recovery after partially completed retirement.

## 0.5.2 -- 2026-09-21

Service runtime consolidation, diagnostics, and authority hardening.

### Added

- Exposed the loaded version and informational PID of the running service in ping and status output, including client/service mismatch warnings.
- Persisted the latest terminal service failure for offline status reporting.

### Changed

- Unified background, foreground, and single-iteration operation around one service-host execution model.
- Scoped retry and automatic-resume authorization to individual scheduler iterations.
- Simplified status projections to expose scheduler eligibility and one structured blocking reason per todo ticket.
- Routed hosted repository authority through the service owner thread and published read-only IPC views.
- Declared Linux as the supported platform for hosted 0.5 execution.

### Fixed

- Hardened background service startup against managed-checkout import shadowing.
- Removed obsolete runtime and compatibility seams after the service-host refactor.

## 0.5.1 -- 2026-09-20

Status, lifecycle, and test hardening.

### Changed

- Improved finite CLI output and status reporting, including concise human output
  and YAML/JSON machine formats.
- Added parallel test/coverage execution and reduced repeated Git setup in the
  test suite.

### Fixed

- Prevented managed repositories from shadowing Devlegate's bundled NanoYAML
  dependency and updated the bundled version to NanoYAML 0.2.1.
- Hardened accepted-ticket integration and recovery so dependent work cannot
  start before integration completes.
- Fixed automatic-resume authorization leaking into later scheduler iterations
  and terminating the service after successful recovery.

## 0.5.0 -- 2026-09-17

Persistent service execution, separated runtime state, and reproducible source distribution.

### Added

- Added a persistent service that owns workflow execution, with CLI clients using local IPC for status and supported operations.
- Added the final service commands: bare `devlegate`, `devlegate foreground`, `devlegate once`, and `devlegate stop`.
- Added SQLite-backed runtime state separate from product history and workflow control history.
- Added interruption recovery, explicit retry, product-base reconciliation, and same-base resume for retained execution progress.
- Added NanoYAML 0.2.0 flow-sequence support for managed workflow data.
- Added CI coverage reporting and publication of the coverage badge.
- Added an explicit distribution model covering Devlegate core, copyable default templates, and the separately licensed NanoYAML dependency.
- Added deterministic full-source release packaging and validation for source trees containing gitlinks.

### Changed

- Separated product, control-plane, and per-ticket execution state and workspaces while keeping Devlegate responsible for lifecycle ownership.
- Routed service-owned status, planning, retry, reconciliation, and shutdown operations through the local service boundary.
- Made full-source archives reproducible and provenance-bearing, with normalized metadata and pinned recursive submodule content.
- Finalized public CLI and service terminology and made repeated service startup requests idempotent when a healthy owner already exists.

### Fixed

- Hardened service ownership, IPC lifecycle, shutdown, retry, reconciliation, and restart handling so recoverable interruptions do not duplicate or lose execution state.

### Removed

- Removed the public `run` and `daemon` service command forms and retired obsolete compatibility seams.

## 0.4.0 -- 2026-09-01

Control-plane isolation and Devlegate-owned execution lifecycle.

### Added

- Added an independent control branch and Devlegate-owned control worktree outside the product checkout.
- Added isolated per-ticket execution worktrees and durable execution branches with lineage binding to product and control revisions.
- Added a typed worker report boundary and durable execution evidence owned by Devlegate.
- Added the `accepted` workflow state and serialized ancestry-only integration of accepted checkpoints into product history.
- Added explicit failed-execution retry with stale-attempt and current-admission validation.
- Added project bootstrap and rendering commands, project-context routing, and packaged Architect and Reviewer protocol artifacts.

### Changed

- Moved canonical workflow state and Git lifecycle ownership off the worker and out of the product checkout.
- Made review and accepted work serial boundaries, and made only `done` work satisfy dependencies.
- Made bootstrap adapt to project-owned documentation and templates instead of overwriting them.

### Fixed

- Failed closed on invalid control topology, worker mutations, publication races, unsafe execution workspaces, malformed reports, divergent integration, and invalid bootstrap state.

### Removed

- Removed obsolete automatic recovery phases, generated-artifact staging, and dead compatibility state from the earlier single-checkout workflow.

## 0.3.0 -- 2026-08-25

Immutable planning, read-only observability, and stricter product boundaries.

### Added

- Added read-only `status` and `plan` commands for workflow, repository, runnable-work, and blocked-work observations.
- Added immutable execution plans carrying the observed repository identity and exact selected-ticket authority into execution.
- Added GitHub Actions CI using the repository-local setup and validation path.

### Changed

- Made observation, planning, and execution use consistent workflow and Git snapshots, including managed-directory state.
- Kept polling alive for blocked workflows while refusing unsafe execution and bounded worker event input before parsing.

### Fixed

- Hardened pending-execution reconciliation, snapshot freshness, runtime branch validation, recovery admission, and unknown-state handling.

### Removed

- Removed the legacy shell runtime, implicit command forms, unbound pending compatibility, and obsolete launcher scaffolding.

## 0.2.4 -- 2026-08-24

Structured tickets, deterministic dependency scheduling, and worker-output safety.

### Added

- Added restricted NanoYAML ticket metadata with canonical ticket identity and explicit backlog, todo, review, and done workflow paths.
- Added dependency validation, cycle detection, and deterministic selection of one runnable ticket whose dependencies are done.
- Added persisted selected-ticket identity and body binding for restart and recovery.

### Changed

- Switched worker execution to headless structured events and rendered worker output as inert terminal data.
- Restricted worker input to the selected assignment and required execution context.

### Fixed

- Prevented terminal-control injection and invalid ticket or dependency graphs from reaching worker execution, and hardened selected-ticket recovery across restart.

## 0.2.3 -- 2026-08-22

Observability and work-generation hardening.

### Added

- Added work-generation identity derived from the observed remote revision and todo contents.
- Added richer dirty-tree and divergent-history diagnostics with persisted generation handling.

### Changed

- Allowed valid local-ahead pending executions to survive restart when their ancestry remained provable.
- Made remote changes and todo changes independently produce new work generations.

### Fixed

- Prevented repeated dispatch of unchanged blocked work, hardened pending-revision reconciliation, and restored exact terminal state after worker execution.

## 0.2.2 -- 2026-08-22

Recovery, synchronization, and preflight hardening.

### Added

- Added side-effect-free preflight checks, user-level state defaults, and deterministic interruption handling during recovery.

### Changed

- Bound pending synchronization to exact persisted revisions and replaced message heuristics with ancestry-based Git reasoning.

### Fixed

- Prevented stale revisions and ambiguous history from being reused, and hardened restart, state-directory, and startup validation before side effects.

## 0.2.1 -- 2026-08-22

Persistent execution state and recovery.

### Added

- Added atomic per-repository state for synchronization and execution intent, including persisted merge-to-agent handoff.
- Added bounded dirty-work recovery, configurable recovery prompts, prompt-variable substitution, and state-directory configuration.

### Changed

- Distinguished normal and recovery execution in durable lifecycle state.

### Fixed

- Preserved synchronization intent across interruption, made state writes atomic, and improved recovery and startup failure visibility.

## 0.2.0 -- 2026-08-22

Python runtime and package distribution for the original bootstrap behavior.

### Added

- Added an installable Python package, `devlegate` console entry point, and `python -m devlegate` support.
- Added the repository-local `dev` helper for setup, checks, linting, execution, and cleanup.
- Added parity tests, Ruff configuration, and explicit development dependencies.

### Changed

- Replaced the shell runtime with Python while preserving polling, synchronization, locking, and OpenCode delegation behavior.
- Kept the shell entry point as a compatibility wrapper and moved development to a local editable environment.

### Fixed

- Hardened helper launchers and added parity coverage for synchronization, agent failures, environment files, and single-pass execution.

## 0.1.0 -- 2026-08-21

First working bootstrap release.

### Added

- Added a foreground polling orchestrator for a configured Git checkout with remote revision observation and fast-forward synchronization.
- Added automatic OpenCode execution for actionable todo work and a single-pass `--once` mode.
- Added project-local `.env` configuration, an example configuration, and per-checkout instance locking.

### Changed

- Made polling the default mode and kept workflow transitions, implementation commits, and publication owned by the implementation agent.

### Fixed

- Hardened synchronization and agent launch conditions and clarified project-local environment-file ownership.
