# Roadmap

This roadmap describes the current development direction for Devlegate. It is
not release history; see [CHANGELOG.md](CHANGELOG.md) for that.

## 0.5 Documentation And Finalization

- **I - Test cleanup: CLOSED.** Test proof boundaries and service-lifetime
  coverage were completed.
- **J - Documentation cleanup: CLOSED.**
  - **J1 - README and product landing page: CLOSED.**
  - **J2 - CLI/help consistency: CLOSED.**
  - **J3 - Architecture, roadmap, and changelog: CLOSED.**
  - **J4 - Remove stale old-runtime documentation: CLOSED.**
  - **J5 - Release-facing documentation and badges: CLOSED.**
- **K - Final audit: CURRENT.**
  - **K1 - Full invariant audit 0.5: CLOSED.**
  - **K2 - Verify absence of bypass mutable paths: CLOSED.**
  - **K3 - rslab2 dogfood: CLOSED.**
  - **K3.5 - Persistent background service UX: CLOSED.**
  - **K3.6 - Explicit control-lineage reconciliation: CLOSED.**
  - **K3.7 - Service identity and CLI UX polish: CLOSED.**
  - **K4 - Full clean test + exact-head CI: PLANNED.**
  - **K5 - Pre-rename blocker review: PLANNED.**
- **L - Total rename from Devlegate to Devlegate: PLANNED.**

The current 0.5 work is focused on making the supported service architecture,
public commands, release notes, and project documentation easy to understand
without changing runtime behavior.

## Completed Foundations

The current branch already includes the major 0.5 foundations: separate product,
control, and execution Git surfaces; a persistent service owner; local IPC
clients; SQLite operational state; explicit retry and reconciliation; process-loss
and shutdown handling; restricted NanoYAML flow sequences; and CI coverage
reporting.

## Deferred Areas

These are known future areas, not dated commitments:

- General parallel worker execution.
- Warm worker processes or a warm worker pool.
