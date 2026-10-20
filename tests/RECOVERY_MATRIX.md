# Recovery Matrix

The process boundary is real: the acceptance tests kill the foreground service
with SIGKILL and start a new service against the same SQLite database, Git
repositories, and worktrees.

| Boundary | Durable evidence | Process B action | Same execution | Worker rerun | Result / fail-closed rule |
| --- | --- | --- | --- | --- | --- |
| idle / before sync | idle plus handled generations | Re-observe Git and continue polling | N/A | No | Restart is inert; invalid Git state blocks |
| merge_pending before effect | A, B, changed paths, control head | Verify A/B and fast-forward exactly to B | N/A | No | Divergence or rollback is fatal |
| merge_pending after effect | Same coordinates; local HEAD is B | Recognize local B and finalize synchronization | N/A | No | Unexpected local generation blocks |
| agent_pending | Ticket, A, control head, execution id, workspace binding | Resume the admitted workspace and launch once | Yes | One | Changed ticket/control identity blocks |
| worker-launch | agent_running, no worker identity | Keep service alive and blocked | No | No | Ownership cannot be proven absent |
| worker-running, matching live | Exact worker identity is live | Keep service alive and blocked | No | No | Never signal or duplicate the live worker |
| worker-running, absent | Exact identity is absent | Normalize process loss and use RESUME policy | No | One RESUME | Product/control generation must still match |
| post-worker | Worker retired; result not durable | Normalize as lost outcome, then explicit/automatic RESUME | No | One RESUME | Never fabricate a worker report |
| checkpointing | Report durable; checkpoint head absent | Recreate or recognize exact checkpoint | Yes | No | Dirty or ambiguous history blocks |
| post-checkpoint | Report and exact H durable | Re-observe product and continue publication | Yes | No | Product movement triggers reconciliation |
| publishing before push | H and expected remote R durable | Observe R, push H, verify H | Yes | No | Unexpected remote history blocks |
| publishing after push | Stage says publishing; remote is H | Recognize H and continue | Yes | No | Remote mismatch blocks |
| post-publication | Remote equals exact H | Re-prove H and enter lifecycle | Yes | No | Remote drift blocks |
| lifecycle before side effect | Report and lifecycle binding durable | Apply one exact lifecycle commit | Yes | No | Invalid report/control lineage blocks |
| lifecycle after side effect | Exact lifecycle commit exists locally/remotely | Observe and reuse the existing commit | Yes | No | Contradictory control history blocks |
| pending reconciliation | A/B/H and evidence ref durable | Preserve pending state; remain operator-visible | N/A | No | No automatic resolution |
| resolved reconciliation | Effective B and resume_required durable | Preserve RESUME requirement | Future new execution | No during restart | Stale H is never published as normal work |
| accepted integration before effect | ticket + H + R durable; local=R, remote=R | Derive and prove exact C from R, then integrate | N/A | No | Missing evidence or intervening control generation blocks |
| accepted product effect | ticket + H + R durable; product effect is published/proven | Re-prove control lineage from R independently | N/A | No | Product success never weakens control-lineage proof |
| accepted control commit effect | ticket + H + R durable; local=R--C, remote=R | Verify exact C and publish its SHA | N/A | No | Only exact C rooted directly at R is publishable |
| accepted control push effect | ticket + H + R durable; remote=R--C | Recognize exact C as already published and clear intent | N/A | No | Remote contradiction blocks |
| mutable receipt after ACK | Receipt is admission evidence only | Preserve receipt; do not dispatch it | N/A | No | New request id is required for liveness |

## Interrupted Fast-Forward Command Traces

The product and control paths intentionally have different interruption
authority. `resolve` and status/identity commands below are local observations
(Tier 1); `merge` is a local checkout/ref mutation (Tier 3). No command in this
recovery path writes a remote repository; a fetch, if present, is a Tier 3
remote read that also writes local objects and tracking refs.

| Path | Before interruption | After interruption | Decision |
| --- | --- | --- | --- |
| Product, normal | `rev-parse remote-ref` (observe target), `merge --ff-only expected-new`, then `branch`, `worktree root`, `status`, `rev-parse HEAD` | No retry | Complete only after exact target and clean checkout are re-observed |
| Product, interrupted before effect | Same commands through interrupted `merge`; then `branch`, `worktree root`, `status`, `rev-parse HEAD`, WAL coordinate checks, `rev-parse remote-ref` | One bounded `merge --ff-only expected-new`, then the same identity/status/HEAD observations | Retry only for exact product `merge_pending` coordinates and unchanged target; never merge the tracking ref |
| Product, interrupted after effect | Same commands through interrupted `merge`; then identity/status/HEAD observations | No merge retry | Complete from the observed exact target |
| Control, normal | `fetch --prune origin/control`, resolve remote and HEAD, ancestry checks, `merge --ff-only origin/control`, then resolve HEAD | No retry | Control sync may complete normally |
| Control, interrupted | Same commands through interrupted control `merge` | No merge retry in the product transport or WAL path | Propagate interruption; a later fresh control sync must establish its own admission |

The eliminated invocation is the former generic control/product callback retry
of `merge --ff-only <tracking-ref>`. The only newly added Git invocations are
the product target resolution before mutation and the post-interruption
branch, worktree-root, status, HEAD, and target observations. They are local
proofs, not effects. The product retry, when authorized, invokes the exact
admitted commit object once; it cannot follow a tracking ref advanced from B
to C. The control path has no retry invocation: its interrupted merge is
propagated, and the next fresh sync re-observes and performs one normal merge.

The `pre-checkpoint` stage is an existing recovery input used by the engine's
explicit interrupted-execution path; the normal worker path persists
`post-worker` and then `checkpointing`. Its process-loss behavior remains the
existing explicit RESUME policy and is covered by the engine recovery tests.

## Accepted Integration R/H/C Model

The accepted-integration section uses local terminology that is distinct from
the product reconciliation A/B/H coordinates:

* `R` is the exact control HEAD persisted at accepted-integration admission.
* `H` is the accepted product checkpoint persisted with the ticket intent.
* `P` is the durable, observed control predecessor for `C`; fresh intents set
  `P == R`, while verified recovery can advance only `P` once.
* `C` is the exact accepted-to-done control commit, with `parent(C) == P`.

The durable intent is `ticket_id + H + R`. Recovery starts from that exact
persisted provenance. `C` is not discovered by arbitrary history search. A
valid `C` somewhere in ancestry does not authorize publishing any descendant;
automatic unpublished recovery is valid only for exact `C` rooted directly at
the durable predecessor `P`.

Replay can adopt a descendant `P` only before the done effect, after
proving `R` is its ancestor, local and remote both equal `P`, the accepted
ticket and its execution-evidence tree are byte-identical at `R` and `P`, each
completed dependency has the same identity at both revisions, and the ticket
remains accepted with all dependencies done. It writes `P` to the
intent before creating `C`; `R` and the execution admission identity remain
immutable. Control publication always leases the observed `P`; a failed push
is accepted only when re-observation proves the exact `C` was published.

| Control topology | Recovery rule |
| --- | --- |
| local=P, remote=P, before control effect | Create and prove exact C from P; no intervening control generation is allowed |
| local=R--C, remote=R | Publish exact SHA C, never generic HEAD |
| local=R--C--X, remote=R | Fail closed; valid C does not authorize descendant X |
| local=R--X, remote=R | Fail closed |
| remote=R--C | Recognize exact C as already published; do not create a second integration commit |
| remote=R--C--D | Prove first transaction descendant C from R; accept integration and do not rewrite or republish history |
| remote=R--X--C | Fail closed because parent(C) is not R; no rebase, cherry-pick, or merge |
| local=C, remote=X, with divergent histories from R | Fail closed; never force push |
