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

Runs pytest. Extra arguments are passed directly to pytest, allowing focused node
IDs or semantic test files; without arguments it runs the full suite.

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

For worker iterations, select focused tests by behavioral domain and proof
boundary, including stronger tests for process, IPC, durable state, or recovery
changes. Before a worker claims completion, its final focused tests, `./dev lint`,
and `git diff --check` must pass. Avoid repeating an unchanged full local suite
just to reproduce CI. Local tests are a cheap defect filter, not complete
regression proof: hosted exact-checkpoint CI runs the complete configured suite
with coverage and lint and is required for independent review and integration.

The real-service auto-resume topology test publishes its rework review from an
independent clone of the remote control branch. A `none` scheduler plan at the
review barrier does not grant exclusive access to the service's control index:
the owner can still synchronize that checkout. Hosted run 44172781916 failed
committing directly in that checkout with `index.lock` present; the log cannot
tell an active owner from a stale lock, and the unchanged second attempt passed.
The focused publisher-index test holds a real `git commit -a` in its pre-commit
hook, verifies that a competing commit fails while that process owns the lock,
and verifies that the independent publisher can commit and push before release.
The auto-resume test fetches the published review checkpoint and its execution
report in the publisher, then lets the service fast-forward its own checkout.
The normal non-forced push and provenance checks still reject conflicting or
invalid publication; no lock removal or retry is involved.

```sh
./dev coverage
```

Runs pytest with statement and branch coverage, then writes the coverage report
and `coverage.json`.

To create and publish a new release branch, choose the version and full source
commit explicitly, then run from the repository:

```sh
python3 tools/publish_release_branch.py X.Y.Z FULL_SOURCE_COMMIT_ID
```

The source project's version must match (an optional `.devN` suffix is allowed).
The command creates a signed-off commit with the matching README badge before
publishing `release/X.Y.Z` to `origin`. It refuses existing branches and uses a
create-only lease to reject concurrent creation. CI publishes the matching badge
after the branch's checks succeed.

```sh
./dev run [args...]
```

Runs the CLI installed in the repository `.venv`.

## Committed workflow observations

`ServiceEngine` owns one lazy `WorkflowProjectionCache(control_repo,
workflow_paths)` on its serialized repository path. Consumers pass the full
local control commit ID already resolved by the caller. The returned
`WorkflowProjection.control_head` always
names that exact commit, even when `graph` is the same immutable object returned
for a different commit. `graph.by_id` retains all canonical claims, including
duplicates. Each claim records its repository-relative path, state, object
identity, and exact UTF-8 text and parsed ticket when available. Parsed ticket
bodies use the same newline normalization as the working-tree store.

The graph cache key is the ordered set of configured state/path pairs and their
committed tree IDs, including absent paths and the mode/object ID of wrong-kind
entries. Changes outside those trees, even within their common parent, do not
invalidate the graph. Reads use one commit verification and one configured-tree
lookup. A miss lists each distinct state tree and reads uncached regular ticket
blobs in one Git batch. Blob entries cache intrinsic content or parse failures;
the graph binds each entry to its current path and validates self-references
again. Tree listings also retain the prior ticket-inspection refusal of
non-regular evidence under workflow directories. Only direct canonical ticket
entries are parsed. The two process-local LRU caches default to four graphs and 2,048 parsed
blobs. A new owner or an evicted entry can reconstruct from local Git objects.

Absent/wrong-kind state directories, malformed tickets and invalid dependencies
or reservations are diagnostics; an invalid graph has no `TicketStore`, no
runnable tickets and no selection. A present empty directory is valid. Git
failures or invalid object responses raise `GitObservationError` without
publishing a partial graph or falling back to another revision. Reads disable
replacement objects and lazy fetch. Already materialized objects may be reused
without rechecking their availability; the result records committed facts, not
object retention, local or remote ref continuity, workspace or runtime identity,
review provenance, or permission to mutate anything.

### Consumer map and observation boundaries

| Consumer | Observation and source |
| --- | --- |
| `_run_iteration_body`, selection, serial review/accepted barriers, dependency and abandonment eligibility | The graph at the exact synchronized control commit. Normal iteration first materializes it after control sync and before planning; it publishes counts, runnable order and queues from this store. Fresh registered-checkout, cleanliness and branch checks and a comparison against working ticket names, kinds and bytes establish that the graph applies. Worker admission repeats the proof before recording the pending execution. |
| `_collect_status_attempt`, `_make_status_snapshot`, `_make_execution_plan`, `check_result`, retry/interrupt candidate views, `status_projection` | Use the same store where working workflow agrees with the sampled commit. Dirty or staged workflow can still be described using a fresh working store. Status retains its before/after Git, runtime and working-file observation and retry protocol. Published status reads only the owner's immutable snapshot. Status-specific dependency explanations derive from the store and runtime state. |
| Forced retry, control descendant adoption, interrupted recovery, reconciliation resume, lifecycle input and accepted predecessor/completion | Use the projection of the applicable current control commit with fresh working-state proof at graph-dependent mutation boundaries. Ancestry, report identity, admitted dependency blobs, CAS, checkout and workspace ownership remain independently verified. |
| Recovery reload/title, historical-report retry handling, workspace retirement and cleanup candidates | Fresh working evidence, including invalid evidence, remains observable. If it matches committed storage these use the same projection; otherwise they validate working ticket files. Resource and durable state proofs are re-observed separately where required. |
| `inspection._ticket` | Committed at the requested revision, independent of checkout/index changes. Uses shared entries and validation diagnostics. Each request still reads ticket history and checks control identity. A historical revision uses its own graph; response data and local runtime annotations are not cached. |
| Lifecycle output validation, `clean_preview.inspect_clean` working evidence, control bootstrap index/files | Intentional transient working/index observations. The lifecycle validates the resulting graph after moving the ticket and before staging. Clean preview independently hashes working evidence and validates the working graph for its blockers. Bootstrap verifies the exact staged layout. |
| `clean_preview` committed tree, `ticket_reset.admit` | The recursive path/object inventory and selected-ticket read bind the workspace reset digest and verify evidence modes, including non-ticket files. Ticket reset uses the selected exact blob with its own lineage and authority checks. These are object proofs, not independent DAG validation or selection. |
| `abandonment_admission`, `abandonment_evidence`, `execution_provenance`, `review_provenance`, inspection execution/prompt, `_prove_bound_ticket_descendant`, `_failed_ticket_history`, execution/log/package-source evidence | Single-ticket, artifact, historical transition and live ownership proofs. Exact object reads, strict modes, history, bounded prompt reads, generation and local evidence checks remain separate. Abandonment admission consumes the scheduler store for runnable eligibility, then freshly proves request/target blobs and resources. `_todo_fingerprint` and reconciliation index/file checks remain separate mutable generation checks. |

The committed store is never proof of cleanliness. The working comparison
includes ignored canonical filenames and bypasses Git stat shortcuts. A clean
checkout with newline conversion can use the committed store after fresh
validation proves the parsed stores equal. A different working graph cannot
authorize use of a committed one. No graph is saved to runtime storage.

### Representative owner read-path measurement

Run `./dev test -q -s tests/test_workflow_consumers.py::test_real_size_owner_read_paths`.
The disposable linked control worktree has 211 tickets with a dependency chain,
five populated workflow commits, packed initial history, and an independent
publisher. Cold means an empty owner cache, not a cold filesystem. Legacy
inspection reproduces the old blob reads and parsing (including the selected
ticket reread), history, and identity checks. Legacy status and idle iteration
use the old working-tree graph load in the same harness; non-graph startup
work is primed before timing the idle loop.

One Linux run with Git 2.56.1 / Python 3.14.10, counting Git commands launched
by the owner (not internal Git children):

| Observation | Elapsed | Git | `cat-file` | Ticket parses |
| --- | ---: | ---: | ---: | ---: |
| Legacy ticket inspection | 677.70 ms | 234 | 212 | 212 |
| Cold ticket inspection | 97.87 ms | 26 | 1 | 211 |
| Warm ticket inspection | 69.76 ms | 23 | 0 | 0 |
| Legacy status | 174.36 ms | 33 | 0 | 211 |
| Warm status | 128.18 ms | 35 | 0 | 0 |
| Legacy idle iteration | 317.27 ms | 57 | 0 | 422 |
| Warm idle iteration | 286.68 ms | 66 | 0 | 0 |
| Independent control sync | 68.39 ms | 17 | 0 | 0 |
| Unrelated commit inspection | 56.90 ms | 23 | 0 | 0 |
| Moved and amended ticket inspection | 73.61 ms | 27 | 1 | 1 |
| Warm status after amendment | 113.48 ms | 35 | 0 | 0 |

Warm inspection uses two graph lookup commands; the other 21 observe control
identity and ticket history. Execution report/review/prompt and workspace
observations are not included. Compared with the old filesystem graph loader,
warm status adds two immutable lookups; idle iteration also checks live control
state, so its total Git count rises in exchange for eliminating repeated
ticket parsing and proving the committed graph applies. An unrelated control
commit shares the graph with distinct attribution; a changed workflow tree
binds new ticket paths and reparses only the edited blob. The test asserts
counts and reuse, not latency thresholds.

### Projection-only measurement

Run `./dev test -q -s tests/test_workflow_projection.py::test_representative_packed_history`.
The disposable SHA-1 repository contains 210 tickets across six states and six
commits, with dependency edges. `git gc --prune=now` packs the initial history;
the unrelated and workflow update commits add loose objects. The baseline
replicates inspection's graph read (one recursive listing, one read and parse per
ticket); it excludes selected-ticket rereads, history and provenance queries.
Timings below are from one local Linux run with Git 2.56.1 and Python 3.14.10;
"cold" means an empty process cache, not a flushed filesystem cache. Timings
exclude fixture setup and are illustrative; the regression asserts counts and
reuse, not elapsed-time limits.

| Observation | Elapsed | Git subprocesses | Ticket content parses |
| --- | ---: | ---: | ---: |
| Per-ticket inspection reads | 574.34 ms | 211 | 210 |
| Cold projection | 48.88 ms | 9 | 210 |
| Warm same commit | 5.51 ms | 2 | 0 |
| Unrelated commit | 5.06 ms | 2 | 0 |
| Two edited blobs and one move | 26.48 ms | 9 | 2 |

The two warm observations share the same graph and make no blob reads. The
update rebuilds the graph, reuses 208 parsed contents (including the moved
ticket), and reads the two edited blobs in one batch.

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
Devlegate project. Set `TICKET_ID` to the ticket's exact ID before running:

```sh
./dev package arch --ticket "$TICKET_ID"
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

Attributing a missing generation token to version skew requires the producing
service's independently recorded installed-artifact identity. Neither the
current installed version nor a checkpoint trailer establishes that identity.

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
