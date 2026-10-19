---
"type": "devlegate.ticket"
"title": "Fail closed on recovery Git observation and evidence races"
"depends_on": ["TASK-078"]
---

## Milestone

Git architecture hardening: recovery semantics and evidence durability for TASK-073.

## Goal

Ensure recovery distinguishes absent refs/objects from Git observation failures, and preserves evidence creation/CAS authority when racing or replaying control-lineage repair.

## Worker test scope — targeted verification, full suite in CI

Use the existing proof map `tests/TEST_BOUNDARIES.md` and test placement guide `tests/TEST_PLACEMENT.md`. Prefer direct typed Git and recovery transaction proofs, then narrowly selected engine-level recovery scenarios:

```sh
./dev test -q tests/test_git.py tests/test_recovery_boundaries.py
./dev test -q tests/test_control_recovery.py -k "reconcile or recovery or lifecycle or evidence"
./dev test -q tests/test_control_workspace.py -k "repair or evidence or branch or worktree"
./dev lint
```

If a new invariant truly requires independent service/restart topology, select only named related tests from `tests/test_cli_topology.py` with `-k`, and report which higher proof layer was needed. A mock-only test does not establish process recovery authority.

**Do not run by default:** unfiltered `./dev test`, `./dev test-parallel`, `./dev check`, `./dev coverage`, all CLI-topology tests or unrelated packaging/distribution tests. Do not time out while repeatedly executing the entire suite. The worker reports selected commands and results; GitHub Actions supplies full exact-SHA coverage and Ruff evidence, which the reviewer must verify before acceptance.

## Context

`recovery_transactions.reconcile_control_lineage()` currently treats every nonzero `rev-parse --verify` of its evidence ref as absence before attempting `update-ref`. `recovery_workspace.py` also performs direct Git observations and mutations. A direct `run_git` call is not intrinsically unsafe under an authorized transaction; prove any owner/shutdown gap before fixing it.

## Required behavior

1. Inventory direct Git call paths in `recovery_transactions.py` and `recovery_workspace.py`, including upstream authorization, lock, runner and shutdown behavior; distinguish proven risks from safely owner-held operations.
2. Use typed ref/object observations or narrowly extend `git.py` so valid absence, malformed result, corrupt/unreadable repository, OS/transport failure and wrong object identity are distinct outcomes.
3. Preserve distinct create-if-absent, expected-old/CAS, and unconditional-update primitives; no replacement of guarded updates by unconditional writes.
4. On evidence creation race, re-observe exact ref identity under the existing admission constraints. Accept an already-present exact pin only when proof matches; fail closed on conflict, unreadability or ambiguous outcomes.
5. Repeat required lineage/remote observations at the established mutation boundaries, preserve the evidence-before-control-reset ordering, and avoid repair effects after failed admission/observation.
6. Use owner-bound transport where the actual runtime path requires it, without introducing speculative new frameworks or changing standalone inspection contracts. Trace full executed commands and classify changed effects.

## Acceptance criteria

- An observation error cannot authorize evidence creation, reset, repair or worktree removal; absent ref and repository failure are distinguishable at the domain boundary.
- Replay and ref races preserve displaced evidence and never replace a conflicting evidence ref.
- No owner/shutdown bypass is introduced or left unexamined in the scoped mutation paths; transaction locking, WAL, leases and CAS remain effective.
- All new Git invocations (including local reads) have explicit before/after trace and justification, with no hidden Tier 3 write or remote round trip.
- Exact-checkpoint full pytest with coverage and Ruff succeeds in GitHub Actions; the worker runs the scoped regressions instead of requiring a local full-suite run.

## Required regressions

- Setup: evidence ref truly absent; action: reconcile control lineage; expected: guarded create-if-absent followed by required revalidation and adoption.
- Setup: evidence ref already contains exact displaced head; action: replay; expected: idempotent proof without overwriting evidence.
- Setup: evidence ref points elsewhere; action: replay; expected: fail closed without control reset.
- Setup: repository unreadable, corrupt, malformed, or Git execution fails during evidence lookup; action: reconcile; expected: observation failure, no create/reset.
- Setup: another writer creates evidence concurrently; action: guarded create loses the race; expected: re-observe exact ref and either prove identical success or stop.
- Setup: evidence pinned but worktree reset interrupted or failed; action: restart recovery; expected: preserve pin, reprove exact target and recover or block safely.
- Setup: unauthorized or interrupted recovery mutation; action: execute; expected: no authority bypass or unproven replay.

## Reviewer decision — 2026-10-17, execution 5619a162: REWORK REQUIRED

**Exact execution:** `5619a16244ec4c00bc0851b3d138175d`; **checkpoint:** `e1dff2e043911766c9955aed4e64474dc9f7deff`; product base `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`. Worker reported `incomplete` solely because source checkout lacked `.venv`; no local focused pytest or Ruff verification was produced. Preserve this checkpoint, previous execution report and code improvements rather than restarting the implementation.

### Independent exact-SHA CI evidence

[GitHub Actions #39011531062](https://github.com/ranmasao/devlegate/actions/runs/39011531062) ran the full suite: **1241 passed, 1 skipped; coverage 78%**. The overall result is **failure**, exclusively because `./dev lint` reports `E501` at **`src/devlegate/recovery_transactions.py:68`**: the `raise ValueError(f"cannot observe control recovery evidence: {error}") from error` line is 93 characters (88 max). Fix this line, run focused Ruff, and require CI green at the new exact SHA.

### Work to retain

- `git.resolve_commit()` maps Git process/OS launch exceptions into `GitObservationError` rather than treating an observation failure as absent.
- `reconcile_control_lineage()` uses typed commit-ref observation and guarded create-if-absent, re-observes the exact pin after attempted create, and refuses conflicting/unreadable values before control reset.
- `repair_bound_workspace()` uses the same guarded evidence-ref creation and re-observation before branch replacement.
- No architectural expansion or new general-purpose recovery framework is required.

### Remaining scoped proof requirements

1. **Fix Ruff E501.** The changed source must pass `./dev lint`. If `.venv` is missing in the worker checkout and tooling is available, first run `./dev setup` (documented in `docs/DEVELOPMENT.md`), then the ticket's targeted test commands and lint. If provisioning cannot be completed, state the precise impediment; do **not** spend the execution budget rerunning the full pytest/coverage suite. Full test and coverage proof belongs to GitHub CI.
2. **Focused negative/race regression evidence** for the affected recovery transactions, not merely the single new launcher-OSError test: absent evidence -> guarded creation; exactly matching existing pin -> no overwrite; conflicting pin -> refusal before reset; unreadable/malformed Git observation -> no create/reset; lost create-if-absent race -> re-observe exact winner, accept only same SHA; failed observation after creation -> fail closed. Cover the analogous workspace evidence path, ensuring evidence is pinned before branch/worktree effect and never overwritten. Existing tests can be reused if they actually exercise these cases; state the exact test names and add focused cases for genuine gaps. Do not duplicate costly independent-service tests.
3. **Executed Git command/effect trace** before/after for `reconcile_control_lineage` and `repair_bound_workspace`, including helper-internal `rev-parse`, `update-ref`, `reset`, worktree operations and repeated reads. New exact-ref re-observations add **Tier 1 local observations**; guarded `update-ref` remains **Tier 3 local mutation**. Distinguish remote I/O and locally mutating effects. Itemize added invocations and why each is needed; document established upstream owner/lock/shutdown boundaries and any unaddressed limitation without broad refactoring.
4. Preserve all original admission, WAL, leases/CAS, evidence-before-reset, worker/recovery guarantees. Do not replace guarded ref updates by unconditional updates. Full CI including Ruff must pass on the exact final checkpoint. Completion of the worker's local full pytest is **not** required.

**Reviewer disposition:** move the SAME `TASK-079` from `review` back to `todo`. Do not edit the worker branch, existing report, checkpoint or product history. TASK-080 and TASK-081 remain dependent until this ticket reaches `done`.

## Curator review — 2026-10-19, execution e2e3545c: REWORK REQUIRED

**Exact checkpoint:** `52da1ec3d28333a061e0f189c82aca88b594a582`
(parent/product base `412fca11d553b5537cb973ef8fa94b6ac6a041b1`);
**execution:** `e2e3545cbe64426490f2cfb7776faa19`;
**admitted control:** `f6d736df9bd554122aee2d6ca070e22c3bf746e2`.
The service completed the lifecycle after a compatible control advancement to
`709b124fa5961b31defacc4fb0404f8de174d2fe`.

### Verified positives

- [GitHub Actions #39202602131](https://github.com/ranmasao/devlegate/actions/runs/39202602131)
  succeeded on the **exact worker checkpoint**: **1289 passed, 1 skipped**,
  overall **78% coverage**, Ruff **all checks passed**. The prior E501 is
  resolved. Worker report says `completed`, with no remaining items.
- The code retains guarded `create_ref_if_absent` (old-value empty), re-observes
  the evidence pin before a control reset or workspace branch replacement, and
  refuses mismatching evidence; newly added control-path tests cover ordinary
  absence, replay, conflict, same/different-winner races, and unreadability
  **after** creation. The existing real-Git workspace success test now checks
  evidence creation -> re-observation -> ref movement ordering.
- `reset_hard()` replaces the inline `reset --hard` with the same effectful
  command; no new generic framework or remote I/O is introduced by this diff.

### Blocker 1: corrupt existing ref is misclassified as absent (real Git reproducer)

`src/devlegate/git.py::_is_missing_revision_error` classifies
`"needed a single revision"` as *absence*. But Git also emits the same
message for a **present ref pointing to a missing/corrupt object**. The changed
`reconcile_control_lineage()` and `repair_bound_workspace()` therefore call
`create_ref_if_absent` after such an observation, when the contract explicitly
requires **observation failure and no attempted evidence creation/reset/repair**.

Independently reproduced with a fresh real Git repository, one empty initial
commit, and a manually written loose ref containing a nonexistent 40-hex
object ID (in a disposable test repo):

```sh
ref=refs/devlegate/recovery/control/probe
mkdir -p .git/refs/devlegate/recovery/control
printf '%040d\n' 1 > ".git/$ref"
git rev-parse --verify "${ref}^{commit}"
# exit 128: fatal: Needed a single revision
git show-ref --verify "$ref"
# exit 128: fatal: git show-ref: bad ref ... (000...001)
```

The **truly absent** ref produces the same `rev-parse` diagnostic, but a
different `show-ref` diagnostic. Consequently `rev-parse`'s error string
cannot independently establish absence. In the current implementation this
causes `resolve_commit` to return `None` and attempts a guarded
`update-ref` despite an existing corrupt evidence ref. The guard prevents
overwriting that specific ref in this repro, but this is still the wrong
typed observation / mutation authorization and fails the explicit TASK-079
acceptance criterion. Do not weaken create-if-absent, CAS, or the subsequent
exact pin check.

**Required:** distinguish ref truly absent from ref present with
unresolvable/wrong object identity, and from Git execution/repository failures,
using narrowly scoped typed observations appropriate to the exact evidence
ref. Add **real Git** regression(s) for missing vs dangling/corrupt existing
ref, asserting `no update-ref`, `no reset`, and `no worktree removal` in
the corrupt case. Repeat at both control and workspace recovery boundaries,
and ensure resolution errors cannot be suppressed by fallback classification.

### Blocker 2: scoped proof matrix and executed Git trace remain incomplete

- The new mock control tests do not prove an **unreadable initial lookup**
  (the only new unreadability injection triggers on evidence read #2,
  after creation). Cover first-read failure explicitly: no create and no
  reset. Add malformed/wrong-object evidence identity tests. In the
  workspace path, the new coverage adds ordering assertions in a positive
  real-Git test, but not explicit absent/replay, competing-create,
  conflicting-pin or unreadable-initial-observation negative/race cases
  required by the prior curator decision. Reuse existing tests when their
  assertions truly cover a case; name them, and add only missing cases.
- No **executed before/after Git command/effect trace** was provided in the
  worker report, ticket, or changed files. The source diff implies one
  additional Tier-1 `rev-parse --verify` re-observation per evidence
  reconciliation path; `reset_hard` replaces the original reset command
  without changing its Git effect, and guarded `update-ref` remains Tier-3
  local mutation. This static inference is **not** the requested executed
  accounting. Supply short reproducible traced runs of
  `reconcile_control_lineage` and `repair_bound_workspace`, including
  helper-internal commands, branch/worktree mutations, unchanged/changed
  counts and justification, and explicit proof of zero added remote calls
  if applicable. Record owner lock/authorization/shutdown seams, without
  broad unrelated refactoring.

### Rework scope and disposition

Retain all useful code and focused tests from checkpoint `52da1ec3`.
Fix the exact initial-observation classification error, complete only the
missing targeted proof and executed trace, run selected local tests + Ruff,
then require full exact-SHA GitHub CI. Do not rerun the full suite repeatedly
in the worker checkout. No force updates or direct product/worker-branch
modifications are authorized by this review.

**Disposition: NOT ACCEPTED.** Move this same TASK-079 **atomically from
`kanban/review` to `kanban/todo`** with the preserved earlier reviewer notes
and this review appended. The newer TASK-083 rework-protocol ticket remains
unmodified in todo. TASK-080 and TASK-081 stay dependent on accepted/integrated
TASK-079.
