"""Small Unix-domain IPC server for the daemon's read-only service views."""

from __future__ import annotations

import socket
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

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            if not self.path.is_socket():
                raise OSError(f"IPC socket path is not a socket: {self.path}")
            self.path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.path))
            listener.listen(8)
            listener.settimeout(0.2)
        except OSError:
            listener.close()
            self.path.unlink(missing_ok=True)
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
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        self._listener = None
        self._thread = None
        self.path.unlink(missing_ok=True)

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
            with connection:
                self._serve_connection(connection)

    def _serve_connection(self, connection: socket.socket) -> None:
        stream = connection.makefile("rwb")
        try:
            while not self._stop.is_set():
                request: IPCRequest | None = None
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
                except (DevlegateError, OSError) as error:
                    response = encode_error_response(
                        request.request_id if request is not None else "",
                        "application_error",
                        str(error),
                    )
                try:
                    send_frame(stream, response)
                except (IPCProtocolError, OSError):
                    return
        finally:
            stream.close()


__all__ = ["UnixIPCServer", "dispatch_read_only"]
