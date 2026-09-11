# H1 Restart Matrix

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
| accepted integration before effect | accepted_integration ticket/checkpoint intent | Re-observe and perform exact integration | N/A | No | Missing evidence blocks |
| accepted product effect | Intent plus product remote at H | Recognize H, continue control transition | N/A | No | Unexpected product generation blocks |
| accepted control commit effect | Intent plus exact local commit | Verify exact commit and publish it | N/A | No | Non-exact local history blocks |
| accepted control push effect | Intent plus exact remote commit | Recognize published commit and clear intent | N/A | No | Remote contradiction blocks |
| mutable receipt after ACK | Receipt is admission evidence only | Preserve receipt; do not dispatch it | N/A | No | New request id is required for liveness |

The `pre-checkpoint` stage is an existing recovery input used by the engine's
explicit interrupted-execution path; the normal worker path persists
`post-worker` and then `checkpointing`. Its process-loss behavior remains the
existing explicit RESUME policy and is covered by the engine recovery tests.
