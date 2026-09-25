# Devlegate

```text
           D | L
---< D E V L E G A T E >---
          S.P.Q.R.

State · Provenance · Quality · Recovery
```

[![CI](https://github.com/ranmasao/devlegate/actions/workflows/ci.yaml/badge.svg)](https://github.com/ranmasao/devlegate/actions/workflows/ci.yaml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/ranmasao/devlegate/badges/coverage.json)](https://github.com/ranmasao/devlegate/actions/workflows/ci.yaml)

Devlegate is a local deterministic control plane and orchestrator for coding
agents. The agent implements changes; Devlegate owns the workflow around them:
selecting work, preserving Git authority and provenance, validating transitions,
and recovering only from durable evidence.

It is not another coding model or chat agent. It is the boundary that makes a
nondeterministic worker operate inside a controlled development process.

## How It Works

```text
ticket
  ↓
Devlegate selects work
  ↓
agent edits an isolated workspace
  ↓
Devlegate preserves the result
  ↓
review
  ↓
accepted integration
  ↓
done
```

Product history, workflow control history, and active ticket work live in
separate Git surfaces. Workers produce implementation and validated evidence;
Devlegate performs publication, workflow movement, review handoff, and accepted
integration. Ambiguous ownership or state fails closed instead of being guessed
through.

## Why Devlegate / SPQR

The useful distinction is simple:

| Coding agent | Devlegate |
| --- | --- |
| Produces implementation | Selects and admits work |
| Works in an assigned workspace | Owns durable state and exact provenance |
| Returns a result | Controls Git publication and integration |
| Supplies evidence | Validates transitions and recovery |

SPQR names the four concrete design commitments:

- **State**: explicit durable workflow and runtime state.
- **Provenance**: know which repository, control revision, workspace, and result produced work.
- **Quality**: validation, review, and acceptance are explicit gates.
- **Recovery**: known evidence may resume work; ambiguity stops for inspection.

Git remains authoritative outside the worker. Worker output is input and
evidence, not workflow authority. Review remains separate from product
integration, and product history remains separate from workflow history.

## Quick Start

Devlegate currently operates on Linux-hosted local Git repositories. From an
existing repository root:

```sh
python -m pip install -e /path/to/devlegate
devlegate init my-project
# configure .env and .devlegate/project.md
devlegate control init
devlegate check
devlegate
```

The normal `devlegate` command chooses automatic systemd supervision when the
current user manager is usable; otherwise it runs attached to the terminal.
Use `devlegate foreground` for an explicitly attached continuous service or
`devlegate once` for one scheduler pass. See [Operations](docs/OPERATIONS.md)
for project adoption, addressing, service control, and recovery commands.

## What Works Today

- Ticket-driven workflow with isolated per-ticket workspaces.
- Persistent service ownership through local Unix IPC.
- Automatic systemd-or-direct hosting, with fail-closed ownership checks.
- Separate review and accepted integration boundaries.
- SQLite operational state kept outside canonical Git history.
- Explicit retry, drop, reconciliation, and evidence-based recovery.

Current limits:

- Hosted execution requires Linux.
- Execution is serial; there is no warm worker pool.
- Workers are ephemeral and external integrations are not part of this release.
- Standalone and Debian distribution proofs target Linux x86_64; public binary
  publication begins with `0.6.0`.

## Development

```sh
./dev setup
./dev check
./dev test-parallel
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md): ownership, runtime, worker, and recovery boundaries.
- [Operations](docs/OPERATIONS.md): projects, services, status, logs, and recovery commands.
- [Pre-install guide](preinst_readme.md): adoption and project-context preparation.
- [Distribution](docs/DISTRIBUTION.md): wheel, standalone, and Debian contracts.
- [Release packaging](docs/RELEASE_PACKAGING.md): artifact construction and publication policy.
- [Roadmap](ROADMAP.md): future engineering direction.
- [Changelog](CHANGELOG.md): shipped product changes.

## Licensing

- Devlegate core: EUPL-1.2 ([LICENSE](LICENSE)).
- Copyable templates, prompts, and skills: CC0-1.0 ([LICENSING.md](LICENSING.md)).
- NanoYAML: upstream MIT terms ([NOTICE](NOTICE)).
