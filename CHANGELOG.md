# Changelog

This file records notable product changes by release. Entries are ordered from
the current release back to the first working release. Each entry describes
behavior shipped by that release; implementation steps superseded before a
release are not separate product changes.

## Unreleased
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

- Removed the legacy shell runtime, implicit pre-0.3 command forms, unbound pending compatibility, and obsolete launcher scaffolding.

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
