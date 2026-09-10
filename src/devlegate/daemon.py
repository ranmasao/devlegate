"""Foreground daemon host and process-local signal handling."""

import signal
import threading
from collections.abc import Callable

from devlegate.ipc_server import UnixIPCServer
from devlegate.service import ServiceEngine


class ShutdownIntent:
    """Process-local signal intent with operator abort precedence."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self.kind: str | None = None

    def request(self, kind: str) -> None:
        if kind == "operator_abort" or self.kind is None:
            self.kind = kind
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)


def run_daemon(engine: ServiceEngine) -> int:
    """Host one service engine in the foreground until a stop signal arrives."""
    if not hasattr(engine, "state_dir"):
        return run_foreground(engine, lambda intent: engine.serve(intent))
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    try:
        server.start()
        return run_foreground(
            engine,
            lambda intent: engine.serve(intent, lock_handle=authority),
        )
    finally:
        server.stop()
        authority.close()


def run_foreground(
    engine: ServiceEngine,
    operation: Callable[[ShutdownIntent], int],
    *,
    wake: Callable[[], None] | None = None,
) -> int:
    """Run one worker-owning foreground operation under signal ownership."""
    stop_intent = ShutdownIntent()
    wake = wake or getattr(engine, "wake", None)

    def request_stop(signum: int, _frame: object) -> None:
        stop_intent.request(
            "operator_abort" if signum == signal.SIGINT else "service_shutdown"
        )
        if wake is not None:
            wake()

    previous: dict[int, Callable[[int, object], object]] = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
        result = operation(stop_intent)
        return 130 if stop_intent.kind == "operator_abort" else result
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
