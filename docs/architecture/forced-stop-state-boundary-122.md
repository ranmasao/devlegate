# Forced-stop state boundary

The implementation is based on product HEAD
`2e684e81f20b8326c85fd9f754b22d03d435fbb6`, plus the changes in this
workspace. This is the exact base SHA, not a claim that the uncommitted changes
have a Git SHA.

The owner can construct a complete replacement from its current state, then
pause before `replace`. IPC dispatch calls lifecycle directly on a connection
thread, outside the IPC connection lock. That thread takes operator then
lifecycle locks, begins worker drain, and saves the abort before signalling.
SQLite's `BEGIN IMMEDIATE` serializes the two row writes, but its revision is
incremented inside each transaction without checking the payload's generation.
Thus the owner's previously constructed replacement can commit last and erase
the accepted abort. The project flock only excludes another process owner.

The engine state-write lock now covers construction, validation, SQLite commit,
and in-memory reference replacement. Forced-stop eligibility and marker
construction use the same lock. If an owner save wins, the stop observes its
result; if the stop wins, later saves observe the abort. A normal completion
cannot erase the abort while force is active; an interrupted completion can
transfer it to the matching durable failure record. The store remains the
transaction owner and no revision retry is introduced.

The lock order is operator -> lifecycle -> state -> SQLite, or receipt -> state
-> SQLite. The state critical section does not acquire operator, lifecycle,
receipt, supervisor or snapshot locks. Stop callbacks execute after releasing
the state lock, although operator and lifecycle locks remain held. Same-thread
save reentry during a store operation fails rather than allowing an outer stale
replacement to overwrite the inner save. Injected callbacks can add edges not
proved by this internal ordering.

A store exception can occur after SQLite committed, so the first error retains
its existing propagation and the engine refuses later saves and forced signals
until restart. Restart reloads the durable row; it cannot determine whether an
external caller observed an acknowledgement or whether a signal was delivered
before a crash. Recovery must use its existing evidence checks. This boundary
does not resolve cleanup inventory or status publication findings in the
TASK-112 audit.
