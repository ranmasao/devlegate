---
"type": "devlegate.ticket"
"title": "Automatically continue requeued ticket after failed execution"
"depends_on": ["TASK-137", "TASK-138"]
---

## Milestone

Normal deferred-ticket lifecycle and worker continuation after infrastructure failure.

## Goal

Make an ordinary, explicitly committed **`backlog → todo`** return sufficient to **attempt automatic safe continuation** of a ticket previously deferred after failed execution. Devlegate should retain the failed worker's committed code, retain genuine earlier curator feedback when present, and launch a new worker generation on the current product base. When required and provably safe, perform **one internally managed Git rebase** of the retained worker delta; require **no additional operator inspection, repair command, execution IDs, SHA assertions, or flags**.

This is the intended user workflow, not a historical-repair CLI subsystem. A conflict or ambiguous ownership remains a diagnosed, fail-closed blocker rather than an invitation to silently destroy code.

## Context

In the concrete TASK-134 history, reviewed execution `0c944f7c8bb741a4a367c95c4cfc10d0` has immutable curator feedback. Later transport-failed execution `3cb61e2d366042aeab97caf3f4702ece` preserved checkpoint `7c8274a837bd5579b3ac607bd570ff8d14e5016b` containing 18 linear worker commits from base `5b81966afab8eac2d021332c22a04a34361736ef`. The product subsequently advanced to `d0572c6f7704b138ed9e7cabb33ba4c025001a89`. The human moved the original ticket `todo → backlog → todo` after the failure. No new manual repair command should have been necessary to attempt continuation.

TASK-137 already fixes stale-failure supersession after a proven requeue; TASK-138 already recognizes the prior review followed by failed execution and intentional deferral. The remaining defect is the scheduler/owner's refusal to continue from the **later failed checkpoint** instead of treating it as an impossible reviewed generation. The original TASK-139 wording incorrectly demanded operator-initiated historical admission and extra authorizations; this is a genuine Architect requirements correction, not ordinary rework hardening. Do not assume code from the never-integrated TASK-139 or TASK-140 execution branches is present in the product.

## Required behavior

1. **Natural trigger, no new command.** A valid canonical ticket is in `todo`, runnable, and the owner proves its exact first-parent `todo → backlog → todo` deferral after the most recent failed execution. This transition supersedes the old failure's scheduler gate (TASK-137) but does not erase the failure or imply that any arbitrary previous workspace is safe. Continue automatically via ordinary scheduling. Do not add `inspect-historical`, `historical-admit`, `--assume-historical-ownership`, `--rewrite-published`, or any other user-facing prerequisite. The operator's normal action remains moving the ticket back to `todo`.
2. **Recover the correct predecessors.** Resolve exact immutable report/control history rather than filenames: the latest relevant **failed** execution supplies the retained code checkpoint and original base; a preceding genuinely reviewed execution supplies its immutable curator feedback only if such a valid review exists. Never misclassify failed transport as reviewed. Preserve the actual failed cumulative worker delta, not only the older reviewed checkpoint. If there is no prior review, continue without fabricating one. The canonical ticket blob and prerequisites must remain valid across the deferral/requeue.
3. **Owner-held safe observation.** Before effects, under the normal serialized owner, verify no live worker/conflicting intent; exact checkpoint and linear first-parent code ancestry; clean product current local/remote head; correct managed worktree registration, common directory, branch/path/HEAD, cleanliness, lock/submodule state and current local/remote execution refs. Historical resources without an old generation-specific BOUND record do **not** acquire one by inference. Treat verifiable Git and current owner observations as eligibility for a bounded continuation decision, never as proof of uninterrupted past ownership; if needed safety cannot be established, stop with an actionable reason and leave all resources intact. No generic reset or forced retry.
4. **At most one necessary rebase.** If code is already based on the current product head, reuse it unchanged. Otherwise pin original failed checkpoint and any displaced published ref before effects, persist a narrowly scoped restart-safe continuation intent, and reuse/factor existing Git reconciliation primitives to run one `git rebase --onto <current-product-head> <failed-base>` of the exact retained worker lineage. The owner, not the user, supplies these identities. Guard local refs by expected-old CAS and any remote publication by an exact `--force-with-lease` internally, only when the old remote is the proven Devlegate-managed expected predecessor. No separate operator flag is required. On conflict abort safely, preserve old lineage and report a blocker. Recovery observes durable stages and does not run the same successful rewrite twice.
5. **Resume the ordinary worker lifecycle.** After a successful/no-op alignment, create a **fresh execution ID** and prepare its worker from the preserved/rebased failed checkpoint, with original curator feedback in the rework prompt when applicable. Do not reset to the older reviewed checkpoint, relabel the failed execution, overwrite reports or reviews, or publish an invented review. The follow-on worker uses normal report/review/integration mechanics. State and scheduler snapshots must not advertise runnable work when an actual resource conflict prevents launch.
6. **Preserve historical evidence.** Old execution reports, control history, original checkpoint/evidence refs, old feedback and ticket spec bytes across the ordinary deferral must remain untouched by execution recovery. No TASK-134-specific production branch, general-purpose historical authorization workflow, migration/compatibility apparatus, or changes to unrelated reset tasks. Never modify actual historical TASK-134 resources in tests.

## Acceptance criteria

- A disposable real-Git `review → todo → failed(transport, committed nonempty delta) → backlog → todo` sequence automatically launches a **new worker** through ordinary owner scheduling without any extra command or flag. The code includes every failed worker commit and the prompt contains the authentic original review.
- With an independently advanced product, the owner performs exactly **one** safe rebase (or none if unnecessary/already aligned) and correctly pins/displaces old code; original failed checkpoint and all immutable reports/reviews survive. An internal remote lease does not require operator authorization flags.
- A valid failed execution without a previous curator review also continues without fabricated feedback when its checkpoint and resources are safe. Repeated restart/poll does not create duplicate workers or rerun a completed rebase.
- Invalid/unproven generation, dirty/foreign/locked/missing worktree, inconsistent remote/local ref, conflicting worker or intent, changed ticket blob or dependency, product/control drift or Git rebase conflict blocks without destructive effects and presents a precise reason.
- No new CLI subcommand, IPC command, forensic operator workflow or explicit per-execution manual authorization is introduced. Existing ordinary retry and active `reconcile update-base` keep their own contracts.

## Required regressions

Use isolated, disposable real-Git repositories and persisted-state/owner tests, not the real TASK-134 resources. Cover success with two or more failed-generation commits plus prior genuine review, base unchanged, base advanced and one rebase, no prior review, publication on managed execution ref, no-op/already integrated where demonstrable, restart before/after pin and rewrite and before new worker, duplicate scheduler iterations, drift, dirty/untracked/locked/foreign/recreated worktree, submodule mismatch, remote lease race, invalid history, modified ticket, no worker left behind on conflict, and unobservable Git/owner states. Assert exact original checkpoint and evidence remain reachable and immutable. Run focused tests, Ruff and exact-checkpoint configured CI.

## Boundaries

This is a normal lifecycle-continuation fix **inside Devlegate**, not a new user-facing recovery interface. The safety boundary still fails closed if resources cannot be sufficiently proved. Prefer a minimal change to existing scheduler, rework preparation and reconciliation rather than an independent historical subsystem. Do not add compatibility/migration paths or TASK-134 special cases.
