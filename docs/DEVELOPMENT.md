# Development

This guide covers work from a Devlegate source checkout. `./dev` is a
source-tree development helper, not part of the installed product interface.
It is not installed by a Debian package, standalone binary archive, or Python
wheel. A full source checkout or source archive naturally contains it.

## Setup

From the repository root:

```sh
./dev setup
```

This creates and owns the repository-local `.venv`, then installs Devlegate and
its development tools into it. Set `PYTHON_BIN` to choose the Python executable
used to create the environment:

```sh
PYTHON_BIN=python3.12 ./dev setup
```

Run setup before the other commands.

## Commands

```sh
./dev test [args...]
```

Runs pytest. Extra arguments are passed directly to pytest.

```sh
./dev test-parallel
```

Runs pytest with four work-stealing workers.

```sh
./dev lint [args...]
```

Runs Ruff over `src`, `tests`, and `tools`. Extra arguments are passed to Ruff.

```sh
./dev check
```

Runs the serial test suite and lint checks, returning a failure if either fails.

```sh
./dev coverage
```

Runs pytest with statement and branch coverage, then writes the coverage report
and `coverage.json`.

```sh
./dev run [args...]
```

Runs the CLI installed in the repository `.venv`.

## Packaging

`./dev package` is the canonical source-tree interface for building validated
distribution artifacts. Run `./dev setup` first. It requires the repository
`.venv`; if it is missing, the command reports that setup is required. The
package graph requires a clean Git worktree and reports the source commit and
project version before building.

```sh
./dev package wheel
./dev package sdist
./dev package python
./dev package standalone
./dev package deb
./dev package full-source
./dev package all
```

All successful final artifacts are published to `dist/`. Use
`--output-dir PATH` to select another output directory. Use `--keep-work` when
debugging a failed build; otherwise the temporary graph workspace is removed.
The command never installs a Debian package, uses `sudo`, or publishes an
artifact.

The targets have distinct meanings:

- `wheel` and `sdist`: Python distributions built through the canonical PEP 517
  project definition. Both are metadata and payload validated.
- `python`: the wheel and sdist aggregate, including isolated pip installation
  proofs for both artifacts.
- `standalone`: a self-contained Linux distribution built from the validated
  canonical wheel, then packaged, validated, and smoke-tested.
- `deb`: a Debian-family package built from the already validated standalone
  package. It is extracted and smoke-tested; it is never installed.
- `full-source`: a complete materialized repository-source archive with pinned
  source dependencies. This is different from the Python sdist.
- `all`: every distribution form above, with dependencies built only once.

The canonical dependency flow is `wheel -> standalone -> deb`. The Python
aggregate selects `wheel + sdist + pip proofs`; `all` selects wheel, sdist,
standalone, Debian, and full-source artifacts.

`./dev` exists only in the source tree and full-source material. It is not
installed as part of the normal wheel, standalone, or Debian runtime packages.

```sh
./dev clean
```

Removes named caches, coverage output, Python caches, and build artifacts.

```sh
./dev purge
```

Runs `clean`, then removes `.venv` and other named development environments.

For contribution rules, licensing requirements, and DCO sign-off, see
[CONTRIBUTING.md](../CONTRIBUTING.md).
