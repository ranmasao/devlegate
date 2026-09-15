"""Foreground daemon host and process-local signal handling."""

import os
import signal
import threading
from collections.abc import Callable

from devlegate.ipc_server import UnixIPCServer
from devlegate.runtime import _log
from devlegate.service import ServiceEngine


class ShutdownIntent:
    """Process-local signal intent with operator abort precedence."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self.kind: str | None = None
        self.source: str | None = None

    def request(self, kind: str, *, source: str | None = None) -> None:
        if kind == "operator_abort" or self.kind is None:
            self.kind = kind
            self.source = source
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)


def _notify_startup(fd: int | None, message: str) -> None:
    if fd is None:
        return
    try:
        os.write(fd, f"{message}\n".encode("utf-8"))
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def run_service(
    engine: ServiceEngine, *, once: bool = False, startup_fd: int | None = None
) -> int:
    """Host one service engine with its runtime authority and IPC endpoint."""
    stop_intent = ShutdownIntent()
    if not hasattr(engine, "state_dir"):
        operation = (
            (lambda intent: engine.serve(intent, once=True))
            if once
            else (lambda intent: engine.serve(intent))
        )
        return run_foreground(
            engine, operation, intent=stop_intent
        )
    authority = engine._lock()

    def request_service_stop() -> None:
        _log("shutdown explicitly requested through devlegate stop")
        stop_intent.request("service_shutdown", source="stop_command")

    server = UnixIPCServer(
        engine,
        engine.ipc_socket_path,
        shutdown=request_service_stop,
    )
    ready = False
    try:
        server.start()
        _notify_startup(startup_fd, "READY")
        startup_fd = None
        ready = True
        result = run_foreground(
            engine,
            lambda intent: engine.serve(
                intent, lock_handle=authority, once=once
            ),
            intent=stop_intent,
        )
        if stop_intent.is_set():
            source = stop_intent.source or "signal"
            _log(f"orderly shutdown complete ({source})")
        return result
    except BaseException as error:
        if not ready:
            _notify_startup(startup_fd, f"FAILED {error}")
            startup_fd = None
        raise
    finally:
        if startup_fd is not None:
            _notify_startup(startup_fd, "FAILED service startup did not complete")
        server.stop()
        authority.close()


def run_foreground(
    engine: ServiceEngine,
    operation: Callable[[ShutdownIntent], int],
    *,
    wake: Callable[[], None] | None = None,
    intent: ShutdownIntent | None = None,
) -> int:
    """Run one worker-owning foreground operation under signal ownership."""
    stop_intent = intent or ShutdownIntent()
    wake = wake or getattr(engine, "wake", None)

    def request_stop(signum: int, _frame: object) -> None:
        source = "operator_abort" if signum == signal.SIGINT else "signal"
        stop_intent.request(
            "operator_abort" if signum == signal.SIGINT else "service_shutdown",
            source=source,
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
