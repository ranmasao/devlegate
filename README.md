# Devlegate

```text
           D | L
---< D E V L E G A T E >---
         S.P.Q.R.

State · Provenance · Quality · Recovery
```

[![CI](https://github.com/ranmasao/devlegate/actions/workflows/ci.yaml/badge.svg)](https://github.com/ranmasao/devlegate/actions/workflows/ci.yaml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/ranmasao/devlegate/badges/coverage.json)](https://github.com/ranmasao/devlegate/actions/workflows/ci.yaml)

Devlegate is a local orchestrator for coding agents.

The agent writes the code. Devlegate chooses the work, prepares an isolated
workspace, controls Git updates, records what happened, sends completed work to
review, and integrates accepted changes. When it cannot safely determine the
current state, it stops instead of guessing.

## How It Works

```text
ticket
  ↓
Devlegate selects work
  ↓
agent works in an isolated workspace
  ↓
result goes to review
  ↓
accepted work is integrated
  ↓
done
```

The agent produces implementation. Devlegate controls the workflow and Git
operations around it. Review and integration are separate steps, and product
history stays separate from workflow history.

## Why Devlegate / SPQR

| Coding agent | Devlegate |
| --- | --- |
| Writes implementation | Selects and tracks work |
| Works in an assigned workspace | Controls Git updates and workflow state |
| Returns a result | Records where the result came from |
| Provides evidence | Validates review, recovery, and integration |

SPQR describes the four design commitments:

- **State**: workflow state is explicit and saved.
- **Provenance**: Devlegate can trace where each result came from.
- **Quality**: checks and review happen before integration.
- **Recovery**: interrupted work resumes only when it is safe to do so.

## Installation

Choose the artifact that fits the environment. These are independent ways to
install Devlegate; none requires a systemd package dependency.

### Debian-family Linux

Requirements: x86_64 / amd64 and a Debian-family Linux system.

The package filename is `devlegate_<version>_amd64.deb`:

```sh
sudo apt install ./devlegate_*.deb
```

### Standalone Linux archive

The self-contained archive targets Linux x86_64 with glibc. It bundles its own
Python runtime and does not require host Python, pip, or a virtual environment.
The archive contains `devlegate-<version>-linux-x86_64/devlegate`:

```sh
tar -xzf devlegate-<version>-linux-x86_64.tar.gz
sudo install -m 755 \
  devlegate-<version>-linux-x86_64/devlegate /usr/local/bin/devlegate
```

### Python wheel

The Python distribution requires Python 3.12 or newer and has no third-party
runtime Python dependencies. Install a built wheel directly:

```sh
python3 -m pip install ./dist/devlegate-<version>-py3-none-any.whl
```

The project does not assume PyPI publication.

### Source checkout

A source checkout is the contributor and development path. It includes the
`./dev` development helper and the source needed to build the other artifacts.
See [Development](docs/DEVELOPMENT.md).

## Quick Start

After installing Devlegate, attach it to an existing Git project:

```sh
cd /path/to/project
devlegate init my-project
# edit the generated .env and .devlegate/project.md
devlegate control init
devlegate check
devlegate
```

If the current user has access to a usable systemd user manager, Devlegate uses
it automatically for the project service. If not, it runs attached to the
terminal instead. See [Project Setup](docs/PROJECT_SETUP.md) and
[Operations](docs/OPERATIONS.md) for details.

## What Works Today

- Local Git repositories with ticket-driven work.
- Isolated workspaces for agent changes.
- Automatic systemd user supervision when available, with attached fallback.
- Separate review and accepted integration steps.
- Local SQLite runtime state outside Git history.
- Explicit retry, drop, reconciliation, and recovery commands.

Current limits:

- Hosted execution requires Linux.
- Execution is serial and workers are ephemeral.
- There is no warm worker pool or external integration layer.
- Standalone and Debian artifacts target Linux x86_64.

## Documentation

- [Project Setup](docs/PROJECT_SETUP.md): attach Devlegate to an existing project.
- [Operations](docs/OPERATIONS.md): addressing, services, status, logs, and recovery.
- [Architecture](docs/ARCHITECTURE.md): system boundaries and worker ownership.
- [Distribution](docs/DISTRIBUTION.md): wheel, standalone, and Debian details.
- [Development](docs/DEVELOPMENT.md): source-tree setup and development commands.
- [Release Packaging](docs/RELEASE_PACKAGING.md): maintainer artifact mechanics.
- [Roadmap](ROADMAP.md): future Devlegate work.
- [Changelog](CHANGELOG.md): shipped changes.
- [Contributing](CONTRIBUTING.md): contribution, licensing, and DCO rules.

## Licensing

- Devlegate core: EUPL-1.2 ([LICENSE](LICENSE)).
- Copyable templates, prompts, and skills: CC0-1.0 ([LICENSING.md](LICENSING.md)).
- NanoYAML: upstream MIT terms ([NOTICE](NOTICE)).
