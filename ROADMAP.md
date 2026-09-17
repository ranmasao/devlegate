# Roadmap

This roadmap describes the current development direction for Devlegate. It is
not release history; see [CHANGELOG.md](CHANGELOG.md) for that. The categories
below are directional and undated unless explicitly stated otherwise.

## Current Baseline

Devlegate currently separates product history, workflow control history, and
per-ticket execution workspaces into distinct Git surfaces. A persistent
service owns mutable workflow operations and communicates with local clients
over Unix IPC, while SQLite stores operational runtime state outside canonical
Git history. Explicit retry and reconciliation operations, execution lineage
validation, process-loss and shutdown handling, and fail-closed recovery form
the current safety model. Restricted NanoYAML flow sequences support applicable
configuration and control data. CI, deterministic full-source packaging, and
current dogfooding support provide the development and distribution baseline.
The licensing baseline is EUPL-1.2 for Devlegate core, CC0-1.0 for copyable
default templates, and the separate upstream MIT license for NanoYAML.

## Near-Term Engineering

- Continue operational hardening discovered through dogfooding and recovery
  exercises.
- Clarify stable public control, client, and worker protocol boundaries before
  adding new integrations.
- Improve worker execution isolation and terminal behavior where real workloads
  require stronger boundaries or PTY semantics.
- Extend packaging and distribution checks beyond the current source-release
  infrastructure as concrete use cases emerge.

## Worker Isolation And Execution

Future worker execution work should preserve the control plane as the sole
mutable workflow authority while isolating agent processes from it. Areas to
evaluate include controlled filesystem and worktree exposure, process and
resource lifecycle limits, container or equivalent sandbox boundaries, explicit
PTY allocation for terminal-oriented tools, and predictable cleanup and
recovery after worker failure. These are architectural concerns, not a
commitment to a particular sandbox implementation.

## Parallelism And Worker Lifecycle

General parallel worker execution remains deferred. Future designs may need
bounded concurrency, explicit worker ownership and lifecycle policy, and clear
tradeoffs between reusable warm processes and clean-session guarantees. Any
warm worker process or worker pool must prevent cross-task state leakage and
remain compatible with deterministic scheduling, lineage, cleanup, and
recovery.

## Integrations

Potential future integration surfaces include GitHub, GitLab, and external CI
automation such as Jenkins or equivalent systems. These should communicate
through stable Devlegate control and protocol boundaries rather than becoming
privileged bypass paths. They are future integration targets, not current
product commitments.

## Packaging And Distribution

Deterministic full-source release packaging already exists and includes the
materialized source dependencies required by that artifact. A possible future
standalone distribution could bundle application bytecode or a Python runtime,
but no bundling technology has been selected. Such work would require an
explicit inventory of third-party runtime licenses and generated notices, plus
reproducible artifact construction where practical. It is separate from the
current source-only release infrastructure.

## Longer-Term / Exploratory

Longer-term exploration may include richer scheduling and policy mechanisms,
plugin or adapter API surfaces, warm-worker architecture, broader external
integrations, and additional deployment or distribution forms. These topics
are deferred and exploratory; they are not assigned to a release or promised
as a specific implementation.

## Separate Projects

Transactional Git is a separate research and development project, not a
Devlegate roadmap deliverable. Devlegate may consume an interface from that
project in the future if one becomes useful and stable, but Transactional Git
is not part of Devlegate's implementation scope.
