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


## Review findings

Execution `9012f936d65c4d5caf729abb4418284b` / checkpoint
`2dcb88b6e746ba5e87e9ab2ddeb5f5299f037553` has the right overall
shape: root `--version` is concise, rich `version` is project-independent,
runtime identity uses stdlib/local evidence, and CI is green.

GitHub CI run `36885020555` reports:

```text
1071 passed, 1 skipped
coverage: 79%
Ruff: all checks passed
```

Two provenance claims are not yet proven strongly enough for the ticket contract.

### Production blocker: Debian marker is not bound to the executable

The package currently writes:

```json
{"distribution":"debian","package":"devlegate"}
```

and runtime classifies a standalone executable as Debian-installed whenever that
marker exists at the expected relative filesystem location.

That proves only that a Debian marker is present. It does **not** prove that the
currently executing standalone payload is the payload installed by that package.

For example, if a real package once installed the marker and
`/usr/bin/devlegate` is later replaced manually with a portable standalone
executable, the current implementation still reports `installer=debian`. This is
the exact path/adjacent-state false positive the ticket requires us to avoid.

Bind the Debian provenance record to the exact packaged executable. A suitable
design is to include a cryptographic digest of the installed standalone payload
(and version/format identity if useful) in the package-owned marker, have
`package_deb.py` generate it from the exact payload copied into the package, have
`validate_deb.py` verify that binding, and have runtime identity require an exact
match before reporting Debian ownership.

Use stdlib-only runtime verification. A missing, malformed, stale, or mismatching
marker must produce `unknown`, never `debian`.

Add regressions for at least:

- exact Debian marker + exact executable -> Debian;
- no marker -> unknown;
- malformed marker -> unknown;
- marker for a different executable / stale digest -> unknown;
- portable standalone placed in a Debian-looking layout must not become Debian
  merely because a marker exists nearby.

### Production blocker: Python distribution metadata is not bound to this invocation

`metadata.distribution("devlegate")` finds a distribution with that project name,
but by itself does not prove that its `.dist-info` describes the Devlegate package
whose code is currently executing.

A source checkout or another import path can shadow an installed Devlegate
distribution while `importlib.metadata` still finds the unrelated installed
`.dist-info`. In that case the current implementation may report its
`INSTALLER` or `direct_url.json` as installation provenance for the wrong
invocation.

Before using installer/direct-url metadata as provenance, prove that the discovered
distribution metadata corresponds to the current Devlegate package location. Handle
both normal installed distributions and PEP 610/editable installs according to
evidence actually available. If that relationship cannot be proven, keep the
installation properties `unknown`.

Do not infer provenance from package name alone.

Add focused regressions for:

- ordinary installed-package metadata bound to the current package;
- missing optional `INSTALLER` / `direct_url.json`;
- editable/direct-url metadata when it really identifies the current source;
- an unrelated same-name installed distribution while current code comes from a
  different source tree -> provenance remains unknown.

### Required runtime-identity regression coverage

The new identity module currently has no dedicated focused test module. In addition
to the two binding issues above, preserve explicit tests for:

- proven standalone detection and non-standalone fallback;
- deterministic OS-release fallback when
  `platform.freedesktop_os_release()` is unavailable/fails;
- rich JSON/YAML containing only project-independent runtime identity;
- concise `--version` remaining exactly one line and side-effect-free.

Do not broaden this into package-manager probing: no runtime pip, dpkg, Git,
systemd, or network dependency.

Return to review with full CI, distribution validation, coverage, and Ruff green.


## Design clarification: build identity vs installation identity

Keep two different kinds of provenance separate.

### Artifact/build identity is intrinsic

Facts that describe the artifact itself should be established by the build boundary,
not guessed later from filesystem paths or host state.

In particular, it is valid for the build to generate distribution metadata such as:

```text
runtime_form = python-package | standalone
build_format = ...
version = ...
```

This generated metadata is a build output. It does **not** violate source
immutability provided the build does not modify tracked source files in the checkout.

The invariant is:

> Build never mutates tracked source. Distribution-specific intrinsic identity is
> introduced only into generated build outputs.

Do not implement this by editing a tracked Python source file in-place during a
build.

### Installation identity is external

How an artifact was installed is a different fact:

```text
installer = pip | uv | debian | unknown
editable = yes | no | unknown
```

That must be proven by the installation boundary.

For the standalone/Debian relationship preserve the stronger existing distribution
property:

```text
source
  -> wheel
  -> one validated standalone executable E
       -> portable standalone archive contains E
       -> Debian package contains the same exact E
```

Do **not** rebuild, rewrite, substitute, or otherwise mutate `E` into a
Debian-specific executable merely to encode `installer=debian`.

Instead:

- the standalone executable carries/proves its intrinsic form as `standalone`;
- the Debian package adds package-owned installation provenance external to the
  executable;
- that provenance is cryptographically bound to the exact packaged executable;
- runtime reports `installer=debian` only when the external binding verifies.

Thus a portable standalone and the Debian payload may be byte-identical while still
producing different installation identity after installation.

A suitable model is:

```text
inside executable:
    runtime_form = standalone

Debian-owned installation record:
    installation = debian
    package = devlegate
    payload_sha256 = SHA256(exact installed executable)
```

The runtime may verify that record with stdlib hashing. Missing or mismatching
installation evidence yields `unknown`.

For Python-package invocation, standard distribution metadata may carry both
artifact/package facts and installer facts, but installer/direct-url claims must
still be bound to the currently imported Devlegate package as described in the
review finding above.

This clarification is not a request to add a generic build-substitution framework.
Prefer the smallest deterministic generated-metadata mechanism that preserves the
validated-artifact invariants.
