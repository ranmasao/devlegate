# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Test-only service composition for subprocess-backed service tests."""

from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--containment-root", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a service command is required")

    import devlegate.execution_containment as containment
    import devlegate.worker_supervisor as supervisor

    def provider():
        return containment.DurableDeterministicContainmentProvider(
            args.containment_root
        )
    containment.default_containment_provider = provider
    supervisor.default_containment_provider = provider

    if command[0] != sys.executable or len(command) < 2:
        parser.error("test service command must use the current Python executable")
    if command[1] == "-c":
        if len(command) < 3:
            parser.error("Python -c service command is incomplete")
        sys.argv = command[1:]
        exec(compile(command[2], "<test-service-command>", "exec"), globals())
        return 0
    if command[1] == "-m":
        if len(command) < 3:
            parser.error("Python -m service command is incomplete")
        sys.argv = command[2:]
        runpy.run_module(command[2], run_name="__main__")
        return 0

    sys.argv = command[1:]
    runpy.run_path(command[1], run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
