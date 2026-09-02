"""Foreground daemon host and process-local signal handling."""

import signal
import threading
from collections.abc import Callable

from devlegate.service import ServiceEngine


def run_daemon(engine: ServiceEngine) -> int:
    """Host one service engine in the foreground until a stop signal arrives."""
    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    previous: dict[int, Callable[[int, object], object]] = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
        return engine.serve(stop_event)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
