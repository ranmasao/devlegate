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

### Lifecycle outcomes and quiescent upgrade

`stop` and `restart` acknowledgements belong to the live IPC instance. While
the owner drains, another request for the same action receives the original
canonical request ID; the opposing action is refused. `stop requested` means
admission only. A force acknowledgement with `force=true` means the runtime
committed the applicable execution interruption and manual-stop retry hold
before giving the worker supervisor permission. A failure or ambiguous write
grants no such permission. A force received after worker retirement does not
reclassify a checkpoint or publication already in progress.

The host writes exact lifecycle evidence under `STATE_DIR/lifecycle`, keyed by
the state key and a digest of the IPC instance and canonical request ID. The
existing state-key-only file is the latest-outcome view used by native-install
inventory. IPC acknowledgements and SIGTERM do not publish `accepted` files.
`completed` for stop records proved worker retirement: the bound generation
WAL's worker-retired receipt, an exact identity and containment observation of
absence, or a durable WAL showing no launch was admitted. The client separately
requires release of the owner lock and, for a managed unit, manager inactivity
with no main process or job. `failed` is a refusal, even if the endpoint and
lock disappear. Missing or indeterminate proof is not success. Internal restart
retains the authority descriptor through exec and pairs the old handoff with the
replacement's readiness. A managed restart submits a bounded nonblocking job
from the proved retiring owner, then the CLI checks the old `retired` outcome
and a different ready instance matching the manager's new main process.
Neither a manager job nor an empty Python worker registry is a retirement proof.

Install matching CLI and daemon artifacts at a quiescent boundary:

1. With the old matched installation, settle any outstanding stop or restart.
   Gracefully stop the resulting owner and record its instance and request.
   Require independent process/containment retirement evidence, the owner
   lock released, and, for systemd, the registered unit inactive with `MainPID=0`
   and no pending job. Socket absence alone is insufficient. Keep other starts
   and manager/job changes out of the upgrade window.
2. Refuse installation over an unresolved lifecycle, worker launch, containment,
   or external effect. Preserve the runtime SQLite/WAL, generation and operation
   receipts, refs/pins, reports and reviews. Resolve uncertain effects with their
   normal observe-first recovery, not by deleting evidence or replaying a push
   by hand. No state conversion is required or authorized by this change.
3. Verify the installed CLI and service executable are from the same validated
   artifact, then use the existing mode's startup command. Require the startup
   report and a fresh `ping` instance with `ready=true` under the expected owner
   lock and, for systemd, the expected active unit/main process with no pending
   job. Confirm `service status` for the intended project, including any manual
   stop hold or recovery blocker, before enabling normal scheduling activity.

The held-worker IPC test measured force admission at roughly 0.09–0.10 s and
owner exit at 1.6–1.8 s locally. A scheduler Git input barrier and a real
`post-receive` barrier each admitted graceful plus force in about 3 ms, with
authority still held until Git returned. Injected loss of the post-push read
left the publication WAL intact; recovery observed the same remote checkpoint
and acknowledgement without re-pushing the execution or launching a worker.
The ordinary mutable validation and receipt barriers delayed force admission
until the operator lock was released (0.10–0.17 s with a deliberate 0.10 s
hold); replay preserved the original SQLite receipt. These are measurements,
not latency guarantees. S2 candidates are the operator lock held across
validation/receipt work, state writer contention during force admission, and
remaining Git/effect latency after admission. A timed-out IPC acknowledgement
can still be uncertain; inspect the live lifecycle and durable evidence.

The fake-manager no-reply case tests the submission timeout and CLI refusal,
not systemd or kernel signal behavior. To exercise the real manager and
delegation, run `DEVLEGATE_TEST_REAL_SYSTEMD=1 ./dev test -q
tests/test_cli_topology.py::test_real_systemd_lifecycle_escalation` on Linux with
a user manager and writable cgroup v2 delegation. This uses uniquely named
transient units and disposable repositories; it does not replace installed
units. The topology checks deactivation, requester loss, the canonical
outcomes, and release of the actual worker cgroup.

The internal [implementation transfer contract](implementation-transfer.md)
documents the isolated preparation, installation and replay boundary shared by
REWORK and active reconciliation.

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
| Forced retry, control descendant adoption, interrupted recovery, automatic reconciliation, lifecycle input and accepted predecessor/completion | Use the projection of the applicable current control commit with fresh working-state proof at graph-dependent mutation boundaries. Ancestry, report identity, admitted dependency blobs, CAS, checkout and workspace ownership remain independently verified. |
| Recovery reload/title, historical-report retry handling, and immediate integrated-workspace retirement | Fresh working evidence, including invalid evidence, remains observable. If it matches committed storage these use the same projection; otherwise they validate working ticket files. Resource and durable state proofs are re-observed separately where required. |
| Workspace inventory, done-ticket cleanup and historical cleanup cohorts | Each screening pass samples the current committed graph against the clean registered checkout once, lazily for ordinary targets. Invalid or changed evidence refuses screening; historical passes retain the cohort-level refusal and non-done blocker. The sample is private to screening. Fresh clean-workflow, registration, resource, runtime and publication proofs precede worktree, local-ref and upstream-ref removal, with independent CAS/lease checks. |
| `inspection._ticket` | Committed at the requested revision, independent of checkout/index changes. Uses shared entries and validation diagnostics. Each request still reads ticket history and checks control identity. A historical revision uses its own graph; response data and local runtime annotations are not cached. |
| Lifecycle output validation, `clean_preview.inspect_clean` working evidence, control bootstrap index/files | Intentional transient working/index observations. The lifecycle validates the resulting graph after moving the ticket and before staging. Clean preview independently hashes working evidence and validates the working graph for its blockers. Bootstrap verifies the exact staged layout. |
| `clean_preview` committed tree, `ticket_reset.admit` | The recursive path/object inventory and selected-ticket read bind the workspace reset digest and verify evidence modes, including non-ticket files. Ticket reset uses the selected exact blob with its own lineage and authority checks. These are object proofs, not independent DAG validation or selection. |
| `abandonment_admission`, `abandonment_evidence`, `execution_provenance`, `review_provenance`, inspection execution/prompt, `_prove_bound_ticket_descendant`, `_failed_ticket_history`, execution/log/package-source evidence | Single-ticket, artifact, historical transition and live ownership proofs. Exact object reads, strict modes, history, bounded prompt reads, generation and local evidence checks remain separate. Abandonment admission consumes the scheduler store for runnable eligibility, then freshly proves request/target blobs and resources. `_todo_fingerprint` and reconciliation index/file checks remain separate mutable generation checks. |

The committed store is never proof of cleanliness. The working comparison
includes ignored canonical filenames and bypasses Git stat shortcuts. A clean
checkout with newline conversion can use the committed store after fresh
validation proves the parsed stores equal. A different working graph cannot
authorize use of a committed one. No graph is saved to runtime storage.

### Reopening authority and consumer audit

`ticket_cycle.py` reads the terminal departure on the canonical first-parent
history. A single commit must have a unique ordinary `done` claim in its parent
and a unique ordinary `todo` claim in its result, both in valid graphs. The move
commit bounds subsequent admissions; the blob alone cannot distinguish cycles.
The owner does not persist a separate cycle registry.

- `runtime.py` bounds REWORK, acceptance and Change Explorer report selection by
  the control admission. Existing descendant validation still protects admitted
  executions. Older exact-execution inspection and prompts use their own
  admission and publication, and the blackboard remains keyed by ticket.
  `clean_preview.py` includes prior reports in the immutable evidence inventory
  but does not let them supply current workspace or acceptance authority.
- Acceptance recovery can acknowledge a proved historical done publication
  without replaying it. Before new admission, the owner can retire any remaining
  prior resources using that done commit, report and the existing generation,
  registration and ref proofs. Durable cleanup cohorts record their control
  observation: branch name and checkpoint alone can coincide across cycles.
- `ticket_reset.py` selects current continuation evidence; it cannot publish a
  reopening. Failure/retry still checks exact execution and generation; queued
  unbound reset and automatic-retry launch grants cannot cross the move. Pending
  mutations still use their original recovery contracts.
- `tools/ticket_source.py` selects only current-cycle reports and bindings. A
  reopened ticket with no current source evidence has nothing to package.
- `abandonment_admission.py` scopes target resources to the current cycle and
  keeps attributable prior-cycle pins out of disposal scope; the terminal
  publication and lease proofs are unchanged.
- `log_reader.py` is deliberately ticket history, ordered by publication. A
  ticket follower spans execution changes until canonical `done` or `abandoned`.
  Exact-execution mode stays bound to one attempt. The log reader does not select
  evidence for admission. The existing DAG uses current `done` for new admissions
  but does not recalculate historical integration of dependents.

### Ticket log follow during control synchronization

The report-evidence error seen during TASK-200 can be reproduced in a disposable
control repository: start with two committed reports, attach a follower, move
the report directory aside, rematerialize it with `git checkout-index`, and
fast-forward to a report from an independent publisher. The prior reports have
identical bytes. The old observer rejected the changed directory identity;
it also treated changed cached file stat metadata as evidence corruption.
This reproduces the error, but does not establish the original incident's
interleaving.

Ticket follow now reads location and report identities from one exact local
control commit, using the existing Git tree and blob readers. A mutable checkout
move, missing worktree file, or recreated inode cannot change that sample.
Each idle poll resolves the commit once. A new control commit checks the ticket
paths and the latest first-parent report publication; the report history is
walked only when the evidence or its publication tip changes. Previously
validated blobs are reused by identity. Removal, changed blob identities,
rewritten publication history, and malformed or unreadable newly referenced
evidence still produce errors. Runtime SQLite and the local logs are read
separately: a departure allows the reader to drain the old log without
inventing a successful outcome or waiting on a report.

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
| Legacy ticket inspection | 649.51 ms | 234 | 212 | 212 |
| Cold ticket inspection | 107.19 ms | 26 | 1 | 211 |
| Warm ticket inspection | 63.10 ms | 23 | 0 | 0 |
| Legacy status | 156.49 ms | 33 | 0 | 211 |
| Warm status | 116.68 ms | 35 | 0 | 0 |
| Legacy idle iteration | 490.64 ms | 57 | 0 | 422 |
| Warm idle iteration | 386.94 ms | 64 | 0 | 0 |
| Independent control sync | 68.84 ms | 17 | 0 | 0 |
| Unrelated commit inspection | 61.45 ms | 23 | 0 | 0 |
| Moved and amended ticket inspection | 80.24 ms | 27 | 1 | 1 |
| Warm status after amendment | 114.40 ms | 35 | 0 | 0 |

Warm inspection uses two graph lookup commands; the other 21 observe control
identity and ticket history. Execution report/review/prompt and workspace
observations are not included. Idle iteration adds two immutable lookups at
planning and two at the independently bracketed status refresh, where a new
control revision can be observed. Planning rechecks status, checked-out branch
and exact canonical ref after matching the live workflow, since control sync
precedes product synchronization. The canonical ref check needs only one exact
identity read: a missing, unreadable or different ref all refuse the observation.
This removes two redundant probes (idle iteration was 66 Git calls). An unrelated control
commit shares the graph with distinct attribution; a changed workflow tree
binds new ticket paths and reparses only the edited blob. The test asserts
counts and reuse, not latency thresholds.

### Inspection during worker supervision

Run `./dev test -q -s tests/test_inspection.py::test_live_worker_inspection`.
The live service fixture builds a packed control history with 212 tickets, a
210-ticket dependency chain across five commits, and a committed report and
saved prompt. It exercises the CLI, then repeats each read three times over
real IPC with the same worker held open, both in normal scheduling and in an
admitted retry. The measurements include IPC and the wait for a worker poll,
but exclude CLI startup. Git Trace2 records subprocesses from the service
process tree during each read; the held worker itself launches no Git.

One Linux run with Git 2.56.1 / Python 3.14.10 (ranges rounded outward):

| Observation | Scheduled IPC | Retry IPC | Git (scheduled / retry) | `cat-file` | `log` |
| --- | ---: | ---: | ---: | ---: | ---: |
| Service | 6–51 ms | 6–50 ms | 0 / 0 | 0 | 0 |
| Ticket | 163–176 ms | 161–170 ms | 23 / 23 | 0 | 1 |
| Historical execution | 341–347 ms | 328–345 ms | 54 / 54 | 10 | 5 |
| Historical execution with `--prompt` | 335–487 ms | 329–345 ms | 54 / 54 | 10 | 5 |
| Current workspace (partial) | 184–196 ms | 243–249 ms | 27 / 40 | 1 / 5 | 2 / 3 |

The ticket's warm graph lookup is two Git calls; the other 21 check control
identity and read ticket history. Execution counts include report, chronology
and saved-prompt provenance; revealing the verified text in this fixture adds
no Git calls. The workspace view skips live worktree and upstream checks. The
retry fixture has a prior failed report for the current ticket, so its partial
workspace view does additional committed-evidence reads. All repeated reads
completed within the existing supervision budget; the test also forces a
separate timeout and checks that published status remains available during it.

### Cleanup screening measurement

`test_cleanup_ref_only_screening_real_git_trace` counts runtime Git launches
against real Trace2 events and instruments the separate projection runner and
workflow reads. With no candidates, discovery alone used four runtime Git
calls and no workflow reads (17 ms). With 32 non-done or missing candidates,
the rework reduced 132 runtime and 67 projection calls, with 32 working scans
(670 ms), to 11 runtime and five cold projection calls with one working scan
(58 ms). Discovery is two registration lists plus local and remote ref lists.
The single workflow sample adds registration, root and branch checks, initial
HEAD, porcelain status, branch recheck and the exact canonical ref check. The
cold projection adds commit/tree lookup, two distinct state-tree listings and
one blob batch. No rejected candidate adds a Git call or workflow scan.

The eligible trace screens one done and 32 ref-only candidates with the same
one workflow sample: 30 runtime calls (the old single-candidate trace used 26)
and five cold projection calls. Applying to the done candidate uses 110 runtime
and eight warm projection calls with four working scans. Relative to the
original 104 runtime calls, four workflow observations add four live calls
each (initial HEAD, cleanliness, branch and ref verification); removing the
duplicate candidate proof between checkout removal and ref retirement saves
ten runtime calls, including one fetch and two remote reads. The retained ref
retirement path independently proves the candidate at each actual ref deletion.

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

### Reinstall the local native package

Run `./dev package native-install` as your normal user from a clean, reviewed
source checkout. This opt-in command selects Debian or Arch from the native
package database's ownership of `/usr/bin/devlegate`, even if a venv shadows
that path. It requires a supported installed native package and `sudo` for
the package manager alone. It does not accept `--ticket`.

The helper records running registered services, requests graceful stops, and
builds and validates the matching package while the services drain. It checks
shutdown before running the installer, verifies the installed payload even for
a same-version reinstall, and restarts only the previously running persistent
services in their recorded hosting modes. Attached `foreground` and `once`
sessions require manual relaunch; the helper prints their commands. Observable
other-user and unregistered services require acknowledgment and are left alone.
They may keep running the old runtime. Linux process visibility is best-effort;
restricted `/proc` metadata or a different PID namespace can conceal services.

Avoid starting services, changing registrations or supervision, or running other
package operations during the update, including while the installer prompts.
The helper re-observes owners before installation but does not hold a global
lock across operator commands or the package manager. A service that has not
proved graceful shutdown is never force-stopped by this command.

The printed `dist/native-install-*/restart-inventory.jsonl` (or the corresponding
subdirectory of `--output-dir`) records the original owners, stop and restart
attempts, and exact validated artifact. On failure, a stopped persistent service
is restored only when its stop and a known-good installed executable can be
proved. If the update is interrupted, first check whether the native manager
finished and verify or repair the installed package. Then check each recorded
project's status before launching: wait for any outstanding graceful stop, do
not duplicate a live owner, and use the recorded unit or original internal
launcher for persistent restoration. Use the printed attached launch commands
for `foreground` and `once`. There is no automatic package rollback.

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
