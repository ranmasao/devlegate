# Development

This guide covers work from a Devlegate source checkout. `./dev` is a
source-tree development helper, not part of the installed product interface.
It is not installed by a Debian package, Arch package, standalone binary
archive, or Python wheel. A full source checkout or source archive naturally
contains it.

## Setup

From the repository root:

```sh
./dev setup
```

In a Git source checkout, setup first initializes and recursively synchronizes
submodules (including NanoYAML) to the exact commits recorded by the currently
checked-out superproject. This works in linked and detached worktrees. It does
not follow submodule branch tips. After a manual pull, fast-forward, or checkout
of the superproject, rerun `./dev setup` to repair clean stale submodule checkouts.

Setup refuses local modifications, untracked content, ignored files that would
be overwritten, and divergent/unpublished submodule commits. Save or move local
files and preserve local commits on a branch, then manually check out the pinned
commit before retrying. Setup never force-resets submodules. Staged gitlink or
local `.gitmodules` changes must also be resolved first. If a pinned commit
cannot be fetched, restore repository access and rerun setup. A synchronization
failure stops setup before environment creation or installation.

After successful synchronization, setup creates and owns the repository-local
`.venv`, then installs Devlegate and its development tools into it. Repeated
setup is safe; already-correct submodules need no fetch. This synchronization is
source-checkout tooling only; installed Devlegate does not perform it.
Full-source archives use their already-materialized vendored sources without Git
or fetching.
Set `PYTHON_BIN` to choose the Python executable used for source preparation and
to create the environment:

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
./dev package arch
./dev package full-source
./dev package all
```

All successful final artifacts are published to `dist/`. Use
`--output-dir PATH` to select another output directory. It receives final
validated artifacts plus the build log and, for standalone-dependent targets,
the standalone build report; intermediate build data stays in the temporary
workspace. The default is `<selected-repository>/dist`, not the caller's
current directory. Use `--keep-work` when debugging a failed build; otherwise
the temporary graph workspace is removed. The retained workspace path is
written to the build log and printed for non-interactive runs.
The command never installs a Debian package, uses `sudo`, or publishes an
artifact.

Python artifacts are built from a temporary copy of the selected checkout's
tracked files, including pinned submodule files. Ignored checkout state such as
an older `build/` tree cannot enter the wheel, and validation compares packaged
Python modules with the selected source before downstream packages are built.

Common options follow the target for the normal command-oriented form:

```sh
./dev package deb --output-dir PATH
./dev package deb --repo PATH
./dev package deb --keep-work
```

`--repo PATH` packages that Devlegate source checkout instead of the checkout
containing `./dev`. The selected repository supplies the source HEAD, version,
`pyproject.toml`, compliance material, packaging tools, and Git identity. It
must satisfy the same clean-worktree rules as the default checkout. This is
useful for intentionally packaging another worktree or source tree.

For local dogfooding before product integration, select a ticket in the invoking
Devlegate project:

```sh
./dev package arch --ticket TASK-089
```

`--ticket ID` also works with the other package targets. It conflicts with an
explicit `--repo`. Resolution uses committed control/execution evidence and
independent local resource-generation receipts, without fetching or opening
runtime SQLite. Stop the project service before capturing source: the command
holds a read-only absence guard during resolution and capture, and refuses an
active owner, an unretired worker, uncertain operations or ambiguous identities.

A verified live ticket worktree supplies its current files, including staged,
unstaged and non-ignored new source files. Dirty source is captured into a private
clean Git snapshot; the original worktree, index and refs are preserved. Ignored
caches are excluded. Symlinks, dirty or unavailable pinned submodules, changing
files and untracked environment/runtime paths are refused. If the worktree has
been retired, the latest independently attributable checkpoint is used. Missing
latest evidence or objects cause refusal, never substitution of product HEAD.

The selected snapshot's packaging graph, version, templates and compliance files
perform the build using the existing development Python. No installation into
the selected worktree is needed. An incompatible source graph must be updated in
the ticket source before it can be packaged.

Ticket artifacts default to the primary project checkout's
`dist/tickets/ID/<checkpoint-or-WIP-digest>/`, separate from release artifacts.
`--output-dir` overrides this destination, outside execution resources.
The retained `devlegate-TARGET-ticket-source.json` and build log record the
ticket, execution, generation, original checkpoint, WIP digest, selected toolchain
digest and artifact digests. A synthetic snapshot commit is explicitly
**build-only** development evidence, not a worker checkpoint, release-tag proof,
product integration or permission to retry/rebase. `--keep-work` also retains the
private source snapshot. This command does not upload, deploy or install packages.

### Missing historical generation identity

After worktree, branch and local receipt retirement, the newest immutable report
must contain both `workspace_head` and `resource_generation`. The generation is
the random 64-hex admission token from the independently recorded resource
intent, not a hash of the checkpoint or branch. A checkpoint SHA, ticket status,
or `Devlegate-Execution` commit trailer cannot replace that proof. Reused
checkpoints can retain an earlier execution's trailer. Missing latest proof is
an error identifying the selected execution; an older successful report is not
selected instead. Packaging errors are printed to stderr in terminals and when
redirected, without a default traceback.

Upgrading the installed runtime does not repair an old immutable report.
Do not edit reports, create replacement receipts, or disturb the ticket's real
runtime resources to make packaging succeed. For manually authorized dogfooding,
independently check the exact checkpoint object and its contents, then create an
isolated checkout outside all execution resources. For example, with `CHECKPOINT`
set to that independently verified full commit ID and `SOURCE` to a local clone
containing its objects, run these commands in a separate scratch directory:

```sh
git clone --no-hardlinks --no-checkout "$SOURCE" dogfood-source
git -C dogfood-source checkout --detach "$CHECKPOINT"
git -C dogfood-source submodule update --init --recursive
git -C dogfood-source rev-parse HEAD
git -C dogfood-source status --porcelain --untracked-files=all
/path/to/developer/checkout/dev package arch --repo "$PWD/dogfood-source"
```

Compare the full printed HEAD with the authorized checkpoint and require empty
status output before building; verify recursive submodule pins too. The package
command enforces its normal clean-source preflight. Keep build output outside
that checkout, or in its ignored output directory. This manual `--repo` package
does not prove ticket ownership and does not make `--ticket` evidence valid.

### Producer and installed-source identity

The owner records the generation intent before creating execution resources.
After worker group retirement, `runtime.py` adds that exact token to the report
before saving the `checkpointing` WAL stage. Adding `workspace_head` preserves
the token. Recovery (`recovery_workflow.py`) deserializes and replaces only the
checkpoint field; the owner verifies the token against the independent intent
before immutable lifecycle publication, including recovered reports. Missing or mismatched
tokens are refused, not synthesized. This applies equally to completed,
incomplete and blocked claims. Normal owner integration and cleanup retire
resources without changing the committed report.

For a suspected stale runtime, distinguish the executable used by the service
from the developer checkout: record its executable hash, `devlegate version`,
and the installed package's `BUILD-PROVENANCE.json` source commit and artifact
hashes. Compare that source commit's producer code, not just its version string,
with the checkout. A running service may still hold the previous executable
after a package upgrade; use the normal owner restart procedure for future
executions. `./dev package` itself uses the invoking checkout's `.venv` and
`tools/build_distribution.py`, so installing a Debian package does not update
that command.

The supplied TASK-089 and TASK-155 historical omissions do not, by themselves,
identify their producing executable. The current source's normal producer sets
the token; recovery previously checked execution metadata without comparing the
generation, which allowed deficient pending reports through that gate. Tests
exercise actual owner publications, crash replay and retirement. Attributing
those historical omissions specifically to version skew still requires the
producing service's independently recorded installed-artifact identity; neither
the current installed version nor a checkpoint trailer establishes it.

`--keep-work` preserves the new temporary workspace on both success and
failure. Its exact path is written to the build log and printed for
non-interactive runs. It is useful for inspecting intermediate artifacts, build
reports, and extracted validation trees. It is not a cache, and later
invocations always create a new workspace.

The ordinary workflow needs none of these options:

```sh
./dev package deb
./dev package all
```

The targets have distinct meanings:

- `wheel` and `sdist`: Python distributions built through the canonical PEP 517
  project definition. Both are metadata and payload validated.
- `python`: the wheel and sdist aggregate, including isolated pip installation
  proofs for both artifacts.
- `standalone`: a self-contained Linux distribution built from the validated
  canonical wheel, then packaged, validated, and smoke-tested.
- `deb`: a Debian-family package built from the already validated standalone
  package. It is extracted and smoke-tested; it is never installed.
- `arch`: an Arch-family x86_64 package built from the already validated
  standalone package. It is extracted and smoke-tested; it is never installed.
- `full-source`: a complete materialized repository-source archive with pinned
  source dependencies. This is different from the Python sdist.
- `all`: every distribution form above, including the Arch package, with
  dependencies built only once.

The canonical dependency flow is `wheel -> standalone -> deb` or `arch`. The Python
aggregate selects `wheel + sdist + pip proofs`; `all` selects wheel, sdist,
standalone, Debian, Arch, and full-source artifacts.

`./dev` exists only in the source tree and full-source material. It is not
installed as part of the normal wheel, standalone, Debian, or Arch runtime
packages.

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
