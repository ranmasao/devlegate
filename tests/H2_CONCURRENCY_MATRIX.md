# H2 Concurrent-client Matrix

The service accepts multiple independent Unix IPC connections concurrently.
Each connection has its own ordered handler; an idle, incomplete, malformed, or
slow client does not hold the accept loop or unrelated clients.

Read-only requests observe stable live state when possible. If active work keeps
changing the project faster than the optimistic observation can complete, status
and plan may return the last immutable owner-published projection. Each response
is internally coherent, but different clients may observe different generations.

Mutable requests remain intentions only at the IPC boundary. The single
`ServiceEngine` owner performs admission and all runtime mutations. Concurrent
mutable requests are not a queue: one may be admitted, while another is
rejected as already pending/running or no longer valid. Request receipts retain
F3 identity and idempotency semantics.

| Concurrent situation | Required behavior |
| --- | --- |
| Idle or incomplete client A; client B connects | B completes without waiting for A |
| Many status, plan, and ping clients | Responses are structurally valid; no durable state or Git ref changes |
| Status during live worker | Observation remains available; worker ownership and attempt count do not change |
| Status during owner retry | Observation remains available while the owner executes the retry |
| Same request ID and same retry semantics | Both clients receive the same admission result; one receipt and one execution |
| Distinct simultaneous retry IDs | At most one owner admission and one worker attempt; the other request is rejected |
| Stale status followed by retry | Owner revalidates current state; stale observation is not authority |
| Shutdown before mutable admission | Pending command is rejected and waiting client is released |
| Shutdown after mutable admission | Existing receipt and owner transaction semantics remain authoritative |
| Malformed or disconnected client | Only that connection is terminated; other clients and the owner continue |

The connection bookkeeping lock protects only the active-connection registry.
It is not held during owner admission, Git or SQLite I/O, or response writes.
Server shutdown stops accepting, closes all active sockets, waits boundedly for
handlers, and removes only the socket endpoint owned by that server.
