# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Persistent service host and process-local signal handling."""

import os
import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from devlegate.ipc_server import UnixIPCServer
from devlegate.runtime import _log
from devlegate.service import ServiceEngine
from devlegate.service_diagnostics import clear, create, write


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
    engine: ServiceEngine,
    *,
    once: bool = False,
    startup_fd: int | None = None,
    startup_report: Callable[[], None] | None = None,
) -> int:
    """Run one service through the canonical process host."""
    return ServiceHost(
        engine,
        once=once,
        startup_fd=startup_fd,
        startup_report=startup_report,
    ).run()


class ServiceHost:
    """Own service authority, IPC, signals, readiness, and orderly teardown."""

    def __init__(
        self,
        engine: ServiceEngine,
        *,
        once: bool = False,
        startup_fd: int | None = None,
        startup_report: Callable[[], None] | None = None,
    ) -> None:
        self.engine = engine
        self.once = once
        self.startup_fd = startup_fd
        self.startup_report = startup_report

    def run(self) -> int:
        stop_intent = ShutdownIntent()
        authority = self.engine._lock()

        def record_failure(error: BaseException, stage: str) -> None:
            try:
                write(self.engine._locator, create(stage, error))
            except BaseException as diagnostic_error:
                _log(f"service failure diagnostic write failed: {diagnostic_error}")

        def request_service_stop() -> None:
            _log("shutdown explicitly requested through devlegate stop")
            stop_intent.request("service_shutdown", source="stop_command")

        server = UnixIPCServer(
            self.engine,
            self.engine.ipc_socket_path,
            shutdown=request_service_stop,
        )
        ready = False
        try:
            with self._signal_ownership(stop_intent):
                server.start()
                prepare_views = getattr(self.engine, "ensure_hosted_views", None)
                if prepare_views is not None:
                    prepare_views()
                if self.startup_report is not None:
                    self.startup_report()
                _notify_startup(self.startup_fd, "READY")
                self.startup_fd = None
                ready = True
                try:
                    clear(self.engine._locator)
                except BaseException as error:
                    _log(f"service failure diagnostic clear failed: {error}")
                result = self._serve_engine(
                    stop_intent, lock_handle=authority
                )
            if stop_intent.is_set():
                source = stop_intent.source or "signal"
                _log(f"orderly shutdown complete ({source})")
            return result
        except BaseException as error:
            if not stop_intent.is_set():
                record_failure(error, "runtime" if ready else "startup")
            if not ready:
                _notify_startup(self.startup_fd, f"FAILED {error}")
                self.startup_fd = None
            raise
        finally:
            if self.startup_fd is not None:
                _notify_startup(
                    self.startup_fd, "FAILED service startup did not complete"
                )
            server.stop()
            authority.close()
            end_owner = getattr(self.engine, "end_hosted_owner", None)
            if end_owner is not None:
                end_owner()

    @contextmanager
    def _signal_ownership(
        self,
        stop_intent: ShutdownIntent,
    ) -> Iterator[None]:
        wake = getattr(self.engine, "wake", None)

        def request_stop(signum: int, _frame: object) -> None:
            source = "operator_abort" if signum == signal.SIGINT else "signal"
            stop_intent.request(
                "operator_abort"
                if signum == signal.SIGINT
                else "service_shutdown",
                source=source,
            )
            if wake is not None:
                wake()

        previous: dict[int, Callable[[int, object], object]] = {}
        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.getsignal(signum)
                signal.signal(signum, request_stop)
            yield
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

    def _serve_engine(
        self,
        stop_intent: ShutdownIntent,
        *,
        lock_handle: object | None = None,
    ) -> int:
        result = self.engine.serve(
            stop_intent, lock_handle=lock_handle, once=self.once
        )
        return 130 if stop_intent.kind == "operator_abort" else result
