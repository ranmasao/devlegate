---
"type": "devlegate.ticket"
"title": "Make lifecycle stop escalation and terminal acknowledgement coherent"
---

## Milestone

Devlegate 0.5.8 architecture hardening after the three-phase audit. Backlog until explicitly scheduled. Independent vertical substitution; do not introduce dependencies merely because tickets share an audit theme.

## Goal

An accepted graceful stop cannot prevent an authorized subsequent forced stop from escalating the same live operation. Admission, durable cancellation permission, worker retirement, terminal outcome and host authority release must remain distinguishable.

## Context

Phase III S1, baseline fb135883ef3ec584f456fddedc8daf7f723f2553. The reported stop then stop --force problem has no established original interleaving. Existing special lifecycle IPC bypasses the normal owner queue and systemd uses --no-block. A deterministic host experiment reproduced accepted -> completed -> accepted for the same canonical request ID: ServiceHost callbacks can write late ACK after completion. Direct/internal and systemd clients use different completion checks; systemd nonblocking manager submission has no timeout. A force request can also wait on owner locks or an in-progress Git operation; unproved retirement must not be reported as success. Historical TASK-037/043 already implemented basic escalation/restart, so retain those contracts rather than reconstructing them.

## Required behavior

- Reproduce the late receipt ordering using a deterministic barrier at the current host boundary, then replace competing accepted/completed writers with a monotonic exact-instance/request outcome contract. Same-intent repeat/escalation uses original canonical lifecycle ID; a conflicting stop/restart is refused. Neither a late ACK nor systemd SIGTERM nor requester loss may downgrade a terminal outcome.
- Preserve independently handled priority lifecycle intake while graceful drain is incomplete; persist exact execution cancellation/manual retry hold before granting manual forced-stop signaling permission. A failed/uncertain durable write must not allow volatile force permission to run ahead. Preserve existing restart semantics, exact worker/cgroup authority and supervisor signal order.
- All hosting/client variants distinguish admission, pending drain, proved retirement, terminal result and released authority. A zero Python worker count, owner loop return, missing socket, lock absence or manager job is not sufficient proof of successful retirement. If proof is unavailable, report explicit blocked/uncertain outcome, retain WAL and refuse false success. No raw PID/systemd kill bypass.
- Bound nonblocking systemd manager submission separately from actual job/drain time. Keep deactivating-unit routing, existing direct/internal/external/once policy, restart handoff and healthy long graceful drain. No global timeout inflation, new supervisor process, general command queue rewrite or arbitrary cancellation of Git children.
- Measure force-admission versus actual completion separately on a held worker and stalled disposable Git/lock path. Fix receipt and lifecycle boundary in this slice; report any residual lock/effect delays as concrete S2 candidates rather than claiming immediate safe completion or widening the ticket to a general async runtime.

## Acceptance criteria

Repeated and escalated lifecycle commands have a single coherent outcome, independent of the first client's survival; successful stop requires the actual required proof. Graceful, force, restart, worker run and normal service startup remain operational in all supported hosting variants. Remove superseded host receipt writes and false completion inference; record any residual bounded-admission issue separately.

## Required regressions

1. Deterministic accepted/completed/late accepted race, including repeated ID and late SIGTERM; terminal outcome remains stable and both clients observe correct canonical identity.
2. Real direct/internal service with held contained worker, graceful then force and repeated force, original CLI exits; exact retirement and manual stop hold, no new worker. Restart cases preserve distinct policy and conflicting intent refusal.
3. Inject failed/uncertain durable cancellation and unobservable/nonempty containment after leader exit; no unauthorized signal or successful completion; preserve evidence and refusal.
4. Exercise managed systemd deactivating path, no-reply manager submission, late graceful signal, terminal proof and client loss. Use isolated real user manager/delegated cgroup if available; document limitations of fake manager evidence rather than claim full topology proof.
5. Stall scheduler Git and a locked ordinary mutable operation separately; observe admission vs safe exit, preserve unknown external effect and exact replay. Verify no lifecycle downgrade, duplicate mutation or worker bypass. Retain existing force-stop, restart, shutdown, packaging and IPC regressions.

## Operational end state and upgrade

The integrated checkpoint must be usable for subsequent self-hosted development without any later ticket. Preserve all unaffected live workflows. Specify the affected active-state/IPC/evidence contracts, the safe quiescent installation boundary, and exact readiness checks. Preserve immutable reports, reviews, pins and necessary independent authority/effect proofs. No production migration, synthetic evidence or mixed-version protocol. If an actual stopped local installation requires conversion of proven state, describe a narrowly scoped one-shot untracked procedure with exact preconditions, backup/dry-run and validation; never invent authority or erase uncertain effects. Focused regressions, Ruff, diff check and exact-checkpoint hosted CI are mandatory.

Settle old lifecycle intent and prove owner/containment gone; install matching CLI/daemon version. No active state schema change by default.