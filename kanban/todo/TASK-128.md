---
"type": "devlegate.ticket"
"title": "Make operator admission timeout outcomes truthful under owner-lock contention"
"depends_on": ["TASK-120"]
---

## Source-grounded concern
TASK-120's exact-SHA audit `docs/architecture/operator-admission-audit-120.md` at product `ff5f7b8ad289da4f2e853fd18005b34f6f7a1778` identifies a race in `runtime.py::_submit_operator_command` (around 1168-1269). Its one-second Event wait expires before the caller can reacquire the operator lock. Owner admission holds that lock through validation/recording and sometimes effects. The caller may acquire it only *after* receipt/effects have committed, then set cancellation and answer "not admitted". Do not assume a timed-out response proves no admission or side effect.

## Scope
- Reinspect the code at the execution base SHA; construct controlled interleavings of queue timeout, claimed validation, receipt save, and effect-before-receipt for drop/force-retry. Prove actual durable receipt/evidence and returned response. Include drain and scheduler claim contention.
- Establish precise response semantics for **definitely not admitted**, **admitted with durable receipt**, and **indeterminate/unknown** if outcome cannot be proved. Do not return a definitive refusal for a possibly committed admission. Recheck completion/receipt under lock or return explicit uncertain state as appropriate; preserve same request-ID coalescing and fingerprint rejection.
- Preserve single-owner ordering, lifecycle drain/abort linearization, at-most-once admission policy, SQLite uncertain writes and WAL/recovery behavior, Git lease/CAS and remote uncertainty. Do not broaden to a lock architecture rewrite, migrations or generic receipt protocol.
- Tests must force lock acquisition after the nominal timeout and prove the admitted-command race; verify response, receipt, external effect evidence and same-ID replay across service reconstruction and at least a focused process-loss/restart scenario. Cover definite queue cancellation and conflicting ID fingerprint. Treat unverified external effects as unknown rather than inventing exactly-once guarantees.

## Deliverable
Narrow fix, deterministic regression evidence and documented remaining uncertainty, with configured CI.
