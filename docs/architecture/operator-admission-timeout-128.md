# Operator admission timeout outcomes

The admission timeout is a coordination wait, not a bound on response time.
After it expires, the submitter acquires the operator lock before deciding:

* A command still in the pending slot is removed and definitely not admitted.
* A published admission result is returned, including its durable receipt for
  mutable commands. The receipt records admission, not completion of later work.
* A claimed command with no published result has an unknown outcome. It is not
  cancelled. Failures once admission recording starts also report an unknown
  outcome, because a write or an earlier effect may have succeeded.

Retry the same request ID and semantics after the service is available. A
matching durable receipt coalesces the request; a different fingerprint is
rejected. Cleanup retains its existing bounded-cohort replay behavior. A missing
receipt is not proof that no effect occurred: drop and force retry can persist
Git evidence before saving the receipt. Their existing identity and evidence
checks govern recovery. A failed SQLite write remains uncertain until restart;
transport loss and unverified remote operations are also unknown. None of these
responses establish exactly-once external effects or workflow completion.

Regression tests exercise timeout during validation, after receipt save, and
after real drop and force-retry evidence but before receipt save. The drop
process-loss test exits the owner process before receipt persistence and checks
evidence and replay after reconstruction. Scheduler and lifecycle contention
continue to use the existing owner and drain linearization tests. These tests do
not cover every SQLite WAL failure, remote failure, or process-loss boundary.
