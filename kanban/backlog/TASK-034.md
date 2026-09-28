---
"type": "devlegate.ticket"
"title": "Separate concise --version output from runtime identity diagnostics"
"depends_on": ["TASK-022"]
---

## Milestone

Next iteration after Devlegate 0.5.5.

## Goal

Support both conventional concise version discovery and a richer project-independent
runtime identity command:

```text
devlegate --version
devlegate version
```

The first is shell-friendly. The second is an operator diagnostic banner describing
the Devlegate runtime itself, not a project or running service instance.

## Context

The current `version` command only emits the program name and `__version__`.
There is no root-level `--version`.

Project/service properties such as project path, ticket state, PID, service instance
ID, or live daemon identity do not belong in a runtime version command: `version`
must work correctly from any directory and without a configured project.

The current distribution contract also explicitly states that the host installation
record does not identify a package manager or artifact. Therefore installation
provenance must not be guessed from host/service state.

## Required behavior

### `devlegate --version`

Add conventional root-level `--version` behavior.

It must:

- print exactly one concise line containing the program name and shipped version,
  for example `devlegate 0.6.0`;
- exit successfully;
- require no project, `.env`, registry entry, daemon, IPC endpoint, Git
  repository, or host-installation record;
- perform no operational side effects;
- remain suitable for scripts and packaging probes.

Do not turn `--version` into an alias for the richer banner.

### `devlegate version`

Make the existing command a runtime identity diagnostic.

Default human-readable output should be a compact banner with, at minimum:

- Devlegate runtime version;
- runtime/distribution form;
- how this invocation is installed in the system, when that fact can be proven;
- operating-system name/version.

Do not include project-local or live-service-local properties such as:

- project/repository identity;
- PID;
- service instance ID;
- current ticket/execution;
- daemon lifecycle state.

The command must work from an arbitrary directory with no project context.

Keep structured output (`--json` / `--yaml`) if it remains consistent with the
same project-independent runtime identity fields.

### Truthful installation provenance

Introduce an explicit, deterministic runtime/distribution identity boundary instead
of inferring installation method from incidental paths.

Distinguish facts such as these where evidence exists:

- Python package invocation;
- Python installer identity exposed by standard distribution metadata, e.g.
  `.dist-info/INSTALLER`;
- editable/direct-url installation when standard metadata proves it;
- standalone/scie runtime;
- Debian package installation versus a manually unpacked standalone executable.

The standalone executable is currently also the payload installed by the Debian
package, so `SCIE` alone cannot prove Debian ownership. If Debian-vs-portable
identity is required, add durable package provenance/ownership evidence to the
Debian distribution boundary or use another exact mechanism. Do not classify an
executable as Debian-installed merely because its path happens to be
`/usr/bin/devlegate`.

When an installation property cannot be proven, emit an explicit neutral value such
as `unknown` rather than guessing.

Do not make the runtime depend on pip, dpkg, systemd, Git, or another external tool
being installed merely to answer `version`.

### Operating-system identity

Use stdlib/local operating-system evidence only.

On Linux, prefer the platform's own OS-release identity where available and use a
deterministic stdlib fallback otherwise. Do not require network access or project
configuration.

Keep OS distribution/version separate from the bundled Python/standalone runtime
identity.

## Acceptance criteria

- `devlegate --version` produces one stable program/version line from any
  directory.
- `devlegate version` produces a richer runtime banner from any directory.
- Neither path attempts project resolution or daemon IPC.
- The rich banner contains Devlegate version, runtime/distribution identity,
  installation provenance when provable, and OS identity.
- It contains no project, PID, service instance, current execution, or daemon state.
- Python-package, standalone, and Debian-installed cases are distinguished only by
  evidence actually owned by their distribution/runtime boundary.
- Unknown provenance is represented explicitly rather than guessed.
- Structured version output, if retained, carries the same semantics.
- Runtime remains stdlib-only.
- Distribution documentation is updated to describe the provenance source used by
  each shipped form.
- Tests, Ruff, coverage, and distribution validation remain green.

## Required regressions

- `--version` is exactly one line and does not initialize project/runtime state.
- `version` works from a temporary directory with no `.env`.
- Version paths do not contact a running daemon.
- Python distribution metadata is interpreted deterministically, including missing
  optional installer/direct-url metadata.
- Standalone runtime is identified without relying on the host Python environment.
- A manually unpacked standalone executable is not falsely reported as
  Debian-installed.
- Debian packaging carries/proves its installation identity through a maintained,
  independently validated boundary.
- Missing OS-release data falls back deterministically.
- JSON/YAML, if supported, does not contain project/PID/instance fields.
