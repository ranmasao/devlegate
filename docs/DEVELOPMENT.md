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
