# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Small Unix-domain IPC server for service views and command submission."""

from __future__ import annotations

import errno
import os
import socket
import stat
import struct
import threading
import time
from collections.abc import Callable
from pathlib import Path

from devlegate import __version__
from devlegate.ipc_protocol import (
    IPCProtocolError,
    IPCRequest,
    encode_error_response,
    encode_success_response,
    parse_request,
    receive_frame,
    send_frame,
)
from devlegate.platform_support import hosted_runtime_supported
from devlegate.runtime import DevlegateError

UNIX_SOCKET_PATH_MAX_BYTES = 107
_SOCKET_DIRECTORY_MODE = 0o700
_SOCKET_MODE = 0o600
_PEER_CREDENTIALS = struct.Struct("3i")
_FALLBACK_DIRECTORY_PREFIX = f".devlegate-sockets-{os.getuid()}-"


def _service_identity() -> dict[str, object]:
    return {
        "service": "devlegate",
        "version": __version__,
        "protocol_version": 1,
        "pid": os.getpid(),
    }


def dispatch_read_only(engine: object, request: IPCRequest) -> dict[str, object]:
    """Dispatch read-only methods through the service API."""
    if request.method == "ping":
        return _service_identity()
    if request.method == "status":
        result = engine.published_status_payload()
        result["service"] = _service_identity()
        return result
    if request.method == "plan":
        return engine.published_plan_view().as_dict()
    if request.method == "retry-candidates":
        return {"candidates": list(engine.published_retry_candidates_view())}
    raise IPCProtocolError("unknown_method", f"unsupported method: {request.method}")


def dispatch_mutation(
    engine: object,
    request: IPCRequest,
    *,
    shutdown: Callable[[], None] | None = None,
) -> dict[str, object]:
    """Validate and submit a mutation without executing it on the IPC thread."""
    if request.method == "stop":
        if request.payload:
            raise IPCProtocolError("invalid_request", "stop payload fields are invalid")
        if shutdown is None:
            raise IPCProtocolError("unknown_method", "unsupported method: stop")
        shutdown()
        return {"accepted": True}
    if request.method == "retry":
        if set(request.payload) != {"ticket_id"}:
            raise IPCProtocolError(
                "invalid_request", "retry payload fields are invalid"
            )
        ticket_id = request.payload["ticket_id"]
        if not isinstance(ticket_id, str) or not ticket_id:
            raise IPCProtocolError(
                "invalid_request", "retry ticket_id must be non-empty text"
            )
        return engine.submit_retry(ticket_id, request_id=request.request_id)
    if request.method == "reconcile-update-base":
        if set(request.payload) != {"ticket_id", "onto"}:
            raise IPCProtocolError(
                "invalid_request",
                "reconciliation payload fields are invalid",
            )
        ticket_id = request.payload["ticket_id"]
        onto = request.payload["onto"]
        if not isinstance(ticket_id, str) or not ticket_id:
            raise IPCProtocolError(
                "invalid_request", "reconciliation ticket_id must be non-empty text"
            )
        if not isinstance(onto, str) or not onto:
            raise IPCProtocolError(
                "invalid_request", "reconciliation onto must be non-empty text"
            )
        return engine.submit_reconcile_update_base(
            ticket_id, onto, request_id=request.request_id
        )
    if request.method == "reconcile-resume":
        if set(request.payload) != {"ticket_id"}:
            raise IPCProtocolError(
                "invalid_request", "reconciliation resume payload fields are invalid"
            )
        ticket_id = request.payload["ticket_id"]
        if not isinstance(ticket_id, str) or not ticket_id:
            raise IPCProtocolError(
                "invalid_request", "reconciliation ticket_id must be non-empty text"
            )
        return engine.submit_reconcile_resume(ticket_id, request_id=request.request_id)
    if request.method == "reconcile-control":
        if set(request.payload) != {"from", "to"}:
            raise IPCProtocolError(
                "invalid_request",
                "control reconciliation payload fields are invalid",
            )
        from_head = request.payload["from"]
        to_head = request.payload["to"]
        if not isinstance(from_head, str) or not from_head:
            raise IPCProtocolError(
                "invalid_request", "control reconciliation from must be non-empty text"
            )
        if not isinstance(to_head, str) or not to_head:
            raise IPCProtocolError(
                "invalid_request", "control reconciliation to must be non-empty text"
            )
        return engine.submit_reconcile_control(
            from_head, to_head, request_id=request.request_id
        )
    raise IPCProtocolError(
        "unknown_method", f"unsupported method: {request.method}"
    )


def dispatch_request(
    engine: object,
    request: IPCRequest,
    *,
    shutdown: Callable[[], None] | None = None,
) -> dict[str, object]:
    """Dispatch a request without duplicating method ownership knowledge."""
    try:
        return dispatch_mutation(engine, request, shutdown=shutdown)
    except IPCProtocolError as error:
        if error.code != "unknown_method":
            raise
        return dispatch_read_only(engine, request)


class UnixIPCServer:
    """Serve independently framed client connections over a project-local socket."""

    def __init__(
        self,
        engine: object,
        socket_path: Path,
        *,
        shutdown: Callable[[], None] | None = None,
    ) -> None:
        self.engine = engine
        self.path = socket_path
        self.shutdown = shutdown
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connection_lock = threading.Lock()
        self._active_connections: dict[int, tuple[socket.socket, threading.Thread]] = {}
        self._bound_identity: tuple[int, int] | None = None

    def start(self) -> None:
        path_bytes = len(str(self.path).encode())
        if path_bytes > UNIX_SOCKET_PATH_MAX_BYTES:
            raise DevlegateError(
                "IPC socket path is too long for Unix-domain sockets: "
                f"{self.path} ({path_bytes} bytes; maximum is "
                f"{UNIX_SOCKET_PATH_MAX_BYTES})"
            )
        self._prepare_socket_directory()
        self._prepare_endpoint()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.path))
            self._bound_identity = self._path_identity()
            listener.listen(8)
            listener.settimeout(0.2)
            os.chmod(self.path, _SOCKET_MODE)
        except OSError:
            listener.close()
            self._remove_owned_socket()
            raise
        self._listener = listener
        self._thread = threading.Thread(
            target=self._serve, name="devlegate-ipc", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        listener = self._listener
        if listener is not None:
            listener.close()
        with self._connection_lock:
            active = tuple(self._active_connections.values())
        for connection, _thread in active:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        deadline = time.monotonic() + 2
        for _connection, handler in active:
            handler.join(timeout=max(0, deadline - time.monotonic()))
        self._listener = None
        self._thread = None
        self._remove_owned_socket()

    def _prepare_socket_directory(self) -> None:
        parent = self.path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            info = os.lstat(parent)
        except OSError as error:
            raise DevlegateError(
                f"cannot prepare IPC socket directory {parent}: {error}"
            ) from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise DevlegateError(
                f"IPC socket directory is not a real directory: {parent}"
            )
        if info.st_uid != os.geteuid():
            raise DevlegateError(f"IPC socket directory has unsafe ownership: {parent}")
        try:
            if stat.S_IMODE(info.st_mode) != _SOCKET_DIRECTORY_MODE:
                os.chmod(parent, _SOCKET_DIRECTORY_MODE)
        except OSError as error:
            raise DevlegateError(
                f"cannot secure IPC socket directory {parent}: {error}"
            ) from error

    def _prepare_endpoint(self) -> None:
        try:
            info = os.lstat(self.path)
        except FileNotFoundError:
            return
        except OSError as error:
            raise DevlegateError(
                f"cannot inspect IPC socket path {self.path}: {error}"
            ) from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISSOCK(info.st_mode):
            raise DevlegateError(f"IPC socket path is not a socket: {self.path}")
        if info.st_uid != os.geteuid():
            raise DevlegateError(f"IPC socket path has unsafe ownership: {self.path}")
        active = self._probe_endpoint()
        if active is None:
            raise DevlegateError(
                f"IPC socket endpoint cannot be safely classified: {self.path}"
            )
        if active:
            raise DevlegateError(f"IPC socket endpoint is active: {self.path}")
        try:
            self.path.unlink()
        except OSError as error:
            raise DevlegateError(
                f"cannot remove stale IPC socket {self.path}: {error}"
            ) from error

    def _probe_endpoint(self) -> bool | None:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.2)
        try:
            probe.connect(str(self.path))
            return True
        except (ConnectionRefusedError, FileNotFoundError):
            return False
        except socket.timeout:
            return None
        except OSError as error:
            if error.errno == errno.ECONNREFUSED:
                return False
            return None
        finally:
            probe.close()

    def _path_identity(self) -> tuple[int, int]:
        info = os.lstat(self.path)
        return info.st_dev, info.st_ino

    def _remove_owned_socket(self) -> None:
        identity = self._bound_identity
        if identity is None:
            return
        try:
            info = os.lstat(self.path)
        except FileNotFoundError:
            self._bound_identity = None
            self._remove_empty_fallback_directory()
            return
        except OSError:
            return
        if (
            info.st_uid == os.geteuid()
            and stat.S_ISSOCK(info.st_mode)
            and (info.st_dev, info.st_ino) == identity
        ):
            try:
                self.path.unlink()
            except OSError:
                return
            self._remove_empty_fallback_directory()
        self._bound_identity = None

    def _remove_empty_fallback_directory(self) -> None:
        parent = self.path.parent
        if parent.parent != Path("/tmp") or not parent.name.startswith(
            _FALLBACK_DIRECTORY_PREFIX
        ):
            return
        suffix = parent.name.removeprefix(_FALLBACK_DIRECTORY_PREFIX)
        if len(suffix) != 8 or any(char not in "0123456789abcdef" for char in suffix):
            return
        try:
            info = os.lstat(parent)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
            ):
                return
            parent.rmdir()
        except OSError:
            pass

    def _serve(self) -> None:
        listener = self._listener
        if listener is None:
            return
        while not self._stop.is_set():
            try:
                connection, _address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                continue
            if not _peer_credentials_are_current_user(connection):
                connection.close()
                continue
            handler = threading.Thread(
                target=self._handle_connection,
                args=(connection,),
                name="devlegate-ipc-client",
                daemon=True,
            )
            with self._connection_lock:
                if self._stop.is_set():
                    connection.close()
                    continue
                self._active_connections[id(connection)] = (connection, handler)
                handler.start()

    def _handle_connection(self, connection: socket.socket) -> None:
        try:
            self._serve_connection(connection)
        finally:
            with self._connection_lock:
                self._active_connections.pop(id(connection), None)
            connection.close()

    def _serve_connection(self, connection: socket.socket) -> None:
        stream = connection.makefile("rwb")
        try:
            while not self._stop.is_set():
                request: IPCRequest | None = None
                fatal = False
                try:
                    payload = receive_frame(stream)
                    if payload is None:
                        return
                    request = parse_request(payload)
                    result = dispatch_request(
                        self.engine, request, shutdown=self.shutdown
                    )
                    response = encode_success_response(request.request_id, result)
                except IPCProtocolError as error:
                    response = encode_error_response(
                        request.request_id if request is not None else "",
                        error.code,
                        error.message,
                    )
                    fatal = error.fatal
                except (DevlegateError, OSError) as error:
                    if self._stop.is_set():
                        return
                    response = encode_error_response(
                        request.request_id if request is not None else "",
                        "application_error",
                        str(error),
                    )
                    fatal = True
                try:
                    send_frame(stream, response)
                except (IPCProtocolError, OSError):
                    return
                if fatal:
                    return
        finally:
            try:
                stream.close()
            except OSError:
                pass


def _peer_credentials_are_current_user(connection: socket.socket) -> bool:
    if not hosted_runtime_supported():
        return False
    try:
        credentials = connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, _PEER_CREDENTIALS.size
        )
        _pid, uid, _gid = _PEER_CREDENTIALS.unpack(credentials)
    except (AttributeError, OSError, struct.error):
        return False
    return uid == os.geteuid()


__all__ = [
    "UnixIPCServer",
    "dispatch_mutation",
    "dispatch_read_only",
    "dispatch_request",
]
