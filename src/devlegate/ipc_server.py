"""Small Unix-domain IPC server for the daemon's read-only service views."""

from __future__ import annotations

import errno
import os
import socket
import stat
import struct
import sys
import threading
from pathlib import Path

from devlegate.ipc_protocol import (
    IPCProtocolError,
    IPCRequest,
    encode_error_response,
    encode_success_response,
    parse_request,
    receive_frame,
    send_frame,
)
from devlegate.runtime import DevlegateError

UNIX_SOCKET_PATH_MAX_BYTES = 107
_SOCKET_DIRECTORY_MODE = 0o700
_SOCKET_MODE = 0o600
_PEER_CREDENTIALS = struct.Struct("3i")


def dispatch_read_only(engine: object, request: IPCRequest) -> dict[str, object]:
    """Dispatch only the read-only E1 methods through the service API."""
    if request.method == "ping":
        return {"service": "devlegate", "protocol_version": 1}
    if request.method == "status":
        view = engine.status_view()
        return view.as_dict()
    if request.method == "plan":
        view = engine.plan_view()
        return view.as_dict()
    raise IPCProtocolError("unknown_method", f"unsupported method: {request.method}")


class UnixIPCServer:
    """Serve one request at a time over a project-local Unix socket."""

    def __init__(self, engine: object, socket_path: Path) -> None:
        self.engine = engine
        self.path = socket_path
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connection_lock = threading.Lock()
        self._active_connection: socket.socket | None = None
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
            connection = self._active_connection
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
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
        self._bound_identity = None

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
            with self._connection_lock:
                self._active_connection = connection
            try:
                self._serve_connection(connection)
            finally:
                with self._connection_lock:
                    self._active_connection = None
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
                    result = dispatch_read_only(self.engine, request)
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
            stream.close()


def _peer_credentials_are_current_user(connection: socket.socket) -> bool:
    if sys.platform != "linux":
        return True
    try:
        credentials = connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, _PEER_CREDENTIALS.size
        )
        _pid, uid, _gid = _PEER_CREDENTIALS.unpack(credentials)
    except (AttributeError, OSError, struct.error):
        return False
    return uid == os.geteuid()


__all__ = ["UnixIPCServer", "dispatch_read_only"]
