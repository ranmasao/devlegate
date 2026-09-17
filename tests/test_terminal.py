# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import os
import pty
import sys
import termios

from devlegate.cli import _preserve_terminal


def test_preserve_terminal_restores_worker_pty_attributes(monkeypatch):
    master, slave = pty.openpty()
    stream = os.fdopen(os.dup(slave), "r")
    monkeypatch.setattr(sys, "stdin", stream)
    original = termios.tcgetattr(slave)
    changed = list(original)
    changed[1] ^= termios.ONLCR
    try:
        with _preserve_terminal():
            termios.tcsetattr(slave, termios.TCSANOW, changed)
            assert termios.tcgetattr(slave) == changed
        assert termios.tcgetattr(slave) == original
    finally:
        stream.close()
        os.close(master)
        os.close(slave)
