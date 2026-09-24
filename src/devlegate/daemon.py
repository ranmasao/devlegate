# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Persistent service host and process-local signal handling."""

import os
import signal
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import Enum

from devlegate.ipc_server import _INSTANCE_ID, UnixIPCServer
from devlegate.launcher import product_launcher
from devlegate.lifecycle_receipt import write as write_lifecycle_receipt
from devlegate.operational_log import service_log
from devlegate.platform_support import HOSTED_RUNTIME_ERROR, hosted_runtime_supported
from devlegate.runtime import DevlegateError
from devlegate.service import ServiceEngine
from devlegate.service_diagnostics import clear, create, write


class ShutdownIntent:
    """Process-local signal intent with operator abort precedence."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self.kind: str | None = None
        self.source: str | None = None

    def request(self, kind: str, *, source: str | None = None) -> None:
        if kind != "operator_abort":
            raise ValueError(f"unsupported shutdown intent: {kind}")
        self.kind = kind
        self.source = source
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)


class HostingMode(str, Enum):  # noqa: UP042 - preserve legacy string behavior
    """Process-lifetime ownership for the canonical service host."""

    DIRECT = "direct"
    INTERNAL = "internal"
    EXTERNAL = "external"


def _notify_startup(fd: int | None, message: str) -> None:
    if fd is None:
        return
    try:
        os.write(fd, f"{message}\n".encode())
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
    host_mode: HostingMode | str = HostingMode.DIRECT,
    once: bool = False,
    startup_fd: int | None = None,
    startup_report: Callable[[], None] | None = None,
    readiness_report: Callable[[], None] | None = None,
) -> int:
    """Run one service through the canonical process host."""
    return ServiceHost(
        engine,
        host_mode=host_mode,
        once=once,
        startup_fd=startup_fd,
        startup_report=startup_report,
        readiness_report=readiness_report,
    ).run()


class ServiceHost:
    """Own service authority, IPC, signals, readiness, and orderly teardown."""

    def __init__(
        self,
        engine: ServiceEngine,
        *,
        host_mode: HostingMode | str = HostingMode.DIRECT,
        once: bool = False,
        startup_fd: int | None = None,
        startup_report: Callable[[], None] | None = None,
        readiness_report: Callable[[], None] | None = None,
    ) -> None:
        self.engine = engine
        try:
            self.host_mode = HostingMode(host_mode)
        except (TypeError, ValueError) as error:
            raise DevlegateError(f"unsupported hosting mode: {host_mode}") from error
        workers = getattr(engine, "_workers", None)
        if workers is not None and hasattr(workers, "show_worker_output"):
            workers.show_worker_output = self.host_mode is HostingMode.DIRECT
        self.once = once
        self.startup_fd = startup_fd
        self.startup_report = startup_report
        self.readiness_report = readiness_report
        self._handoff_request_id = os.environ.pop("DEVLEGATE_RESTART_REQUEST", None)
        self._handoff_instance_id = os.environ.pop("DEVLEGATE_RESTART_INSTANCE", None)
        self._handoff_authority_fd = os.environ.get("DEVLEGATE_RESTART_AUTHORITY_FD")
        self._handoff_authority_key = os.environ.get("DEVLEGATE_RESTART_AUTHORITY_KEY")
        if (
            self._handoff_authority_fd is not None
            and self.host_mode is not HostingMode.INTERNAL
        ):
            raise DevlegateError(
                "restart authority handoff requires internal hosting mode"
            )

    def run(self) -> int:
        if not hosted_runtime_supported():
            raise DevlegateError(HOSTED_RUNTIME_ERROR)
        stop_intent = ShutdownIntent()
        authority = self._acquire_authority()
        restart_requested = False
        lifecycle_receipt_ready = threading.Event()

        def record_failure(error: BaseException, stage: str) -> None:
            try:
                write(self.engine._locator, create(stage, error))
            except BaseException as diagnostic_error:
                service_log(
                    f"service failure diagnostic write failed: {diagnostic_error}"
                )

        def request_lifecycle(intent: str, request_id: str) -> dict[str, object]:
            if intent == "restart" and self.host_mode is not HostingMode.INTERNAL:
                raise DevlegateError(
                    "restart is available only for internally hosted services"
                )
            service_log(f"lifecycle {intent} accepted through devlegate {intent}")
            result = self.engine.request_lifecycle(intent, request_id)
            write_lifecycle_receipt(
                self.engine._locator,
                {
                    "request_id": result["request_id"],
                    "instance_id": _INSTANCE_ID,
                    "action": intent,
                    "state": "accepted",
                },
            )
            lifecycle_receipt_ready.set()
            return result

        server = UnixIPCServer(
            self.engine,
            self.engine.ipc_socket_path,
            lifecycle=request_lifecycle,
        )
        ready = False
        try:
            with self._signal_ownership(stop_intent, request_lifecycle):
                server.start()
                prepare_views = getattr(self.engine, "ensure_hosted_views", None)
                if prepare_views is not None:
                    prepare_views()
                if self.startup_report is not None:
                    self.startup_report()
                if self._handoff_request_id is not None:
                    if self._handoff_instance_id is None:
                        raise DevlegateError(
                            "restart handoff has no old instance identity"
                        )
                    write_lifecycle_receipt(
                        self.engine._locator,
                        {
                            "request_id": self._handoff_request_id,
                            "instance_id": self._handoff_instance_id,
                            "replacement_instance_id": _INSTANCE_ID,
                            "action": "restart",
                            "state": "completed",
                        },
                    )
                mark_ready = getattr(self.engine, "mark_service_ready", None)
                if mark_ready is not None:
                    mark_ready()
                if self.readiness_report is not None:
                    self.readiness_report()
                _notify_startup(self.startup_fd, "READY")
                self.startup_fd = None
                ready = True
                try:
                    clear(self.engine._locator)
                except BaseException as error:
                    service_log(f"service failure diagnostic clear failed: {error}")
                result = self._serve_engine(
                    stop_intent, lock_handle=authority
                )
                restart_requested = self.engine.lifecycle_intent() == "restart"
                if self.engine.lifecycle_intent() == "stop":
                    request_id = self.engine.lifecycle_status_payload()["request_id"]
                    if not isinstance(request_id, str):
                        raise DevlegateError(
                            "stop lifecycle has no request identity"
                        )
                    if not lifecycle_receipt_ready.wait(timeout=10):
                        raise DevlegateError(
                            "stop lifecycle acceptance was not persisted"
                        )
                    write_lifecycle_receipt(
                        self.engine._locator,
                        {
                            "request_id": request_id,
                            "instance_id": _INSTANCE_ID,
                            "action": "stop",
                            "state": "completed",
                        },
                    )
            if stop_intent.is_set():
                source = stop_intent.source or "signal"
                service_log(f"orderly shutdown complete ({source})")
            elif self.engine.lifecycle_intent() is not None:
                service_log(
                    f"orderly {self.engine.lifecycle_intent()} complete"
                )
            return result
        except BaseException as error:
            if self._handoff_request_id is not None and not ready:
                write_lifecycle_receipt(
                    self.engine._locator,
                    {
                        "request_id": self._handoff_request_id,
                        "instance_id": self._handoff_instance_id,
                        "action": "restart",
                        "state": "failed",
                        "error": str(error),
                    },
                )
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
            end_owner = getattr(self.engine, "end_hosted_owner", None)
            if end_owner is not None:
                end_owner()
            if restart_requested:
                if self.host_mode is not HostingMode.INTERNAL:
                    raise DevlegateError(
                        "non-internal hosting cannot self-reexec for restart"
                    )
                request_id = self._handoff_request_id or ""
                try:
                    lifecycle_request_id = self.engine.lifecycle_status_payload()[
                        "request_id"
                    ]
                    if not isinstance(lifecycle_request_id, str):
                        raise DevlegateError(
                            "restart lifecycle has no request identity"
                        )
                    request_id = lifecycle_request_id
                    write_lifecycle_receipt(
                        self.engine._locator,
                        {
                            "request_id": request_id,
                            "instance_id": _INSTANCE_ID,
                            "action": "restart",
                            "state": "handoff",
                        },
                    )
                    self._reexec(authority, request_id)
                except BaseException as error:
                    write_lifecycle_receipt(
                        self.engine._locator,
                        {
                            "request_id": request_id,
                            "instance_id": _INSTANCE_ID,
                            "action": "restart",
                            "state": "failed",
                            "error": str(error),
                        },
                    )
                    record_failure(error, "restart")
                    authority.close()
                    raise
            else:
                authority.close()

    def _acquire_authority(self):
        if self._handoff_authority_fd is None:
            return self.engine._lock()
        if self._handoff_authority_key != self.engine._locator.state_key:
            if self._handoff_request_id is not None:
                write_lifecycle_receipt(
                    self.engine._locator,
                    {
                        "request_id": self._handoff_request_id,
                        "instance_id": self._handoff_instance_id,
                        "action": "restart",
                        "state": "failed",
                        "error": "restart authority key does not match this runtime",
                    },
                )
            raise DevlegateError("restart authority key does not match this runtime")
        try:
            fd = int(self._handoff_authority_fd)
            descriptor_stat = os.fstat(fd)
            lock_stat = os.stat(self.engine._locator.lock_path)
            if (descriptor_stat.st_dev, descriptor_stat.st_ino) != (
                lock_stat.st_dev,
                lock_stat.st_ino,
            ):
                raise DevlegateError("restart authority fd is not this runtime lock")
            handle = os.fdopen(fd, "w")
            os.set_inheritable(fd, False)
            os.environ.pop("DEVLEGATE_RESTART_AUTHORITY_FD", None)
            os.environ.pop("DEVLEGATE_RESTART_AUTHORITY_KEY", None)
            return handle
        except (ValueError, OSError) as error:
            if self._handoff_request_id is not None:
                write_lifecycle_receipt(
                    self.engine._locator,
                    {
                        "request_id": self._handoff_request_id,
                        "instance_id": self._handoff_instance_id,
                        "action": "restart",
                        "state": "failed",
                        "error": str(error),
                    },
                )
            raise DevlegateError(f"cannot adopt restart authority: {error}") from error

    @contextmanager
    def _signal_ownership(
        self,
        stop_intent: ShutdownIntent,
        request_lifecycle: Callable[[str, str], dict[str, object]],
    ) -> Iterator[None]:
        wake = getattr(self.engine, "wake", None)

        def request_stop(signum: int, _frame: object) -> None:
            if signum == signal.SIGINT:
                stop_intent.request("operator_abort", source="operator_abort")
            else:
                try:
                    request_lifecycle("stop", f"signal-{uuid.uuid4().hex}")
                except DevlegateError as error:
                    service_log(f"graceful SIGTERM request failed: {error}")
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

    def _reexec(self, authority: object, request_id: str) -> None:
        """Replace the internally hosted service with a fresh Python image."""
        if self.host_mode is not HostingMode.INTERNAL:
            raise DevlegateError("only internal hosting can self-reexec")
        fd = authority.fileno()
        os.set_inheritable(fd, True)
        environment = dict(os.environ)
        environment.pop("DEVLEGATE_STARTUP_FD", None)
        environment["DEVLEGATE_HOST_MODE"] = HostingMode.INTERNAL.value
        environment["DEVLEGATE_RESTART_AUTHORITY_FD"] = str(fd)
        environment["DEVLEGATE_RESTART_AUTHORITY_KEY"] = self.engine._locator.state_key
        environment["DEVLEGATE_RESTART_REQUEST"] = request_id
        environment["DEVLEGATE_RESTART_INSTANCE"] = _INSTANCE_ID
        command = product_launcher().argv(
            "--env", str(self.engine.env_file), "foreground"
        )
        os.execvpe(command[0], command, environment)

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
