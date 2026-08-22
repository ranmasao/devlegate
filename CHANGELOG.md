# Changelog

This file records notable product changes by release. Entries are ordered from
the current release back to the first working release. Each entry describes
behavior shipped by that release; implementation steps superseded before a
release are not separate product changes.

## Unreleased
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
