# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Trusted cgroup joiner used to establish the execution boundary."""

from __future__ import annotations

import os
import sys


def main() -> int:
    arguments = sys.argv[1:]
    if len(arguments) < 3 or arguments[0] != "--cgroup" or "--" not in arguments:
        print(
            "usage: execution_launcher --cgroup PATH -- PROGRAM [ARG ...]",
            file=sys.stderr,
        )
        return 2
    separator = arguments.index("--")
    if separator != 2 or separator == len(arguments) - 1:
        print("invalid execution launcher arguments", file=sys.stderr)
        return 2
    cgroup = arguments[1]
    command = arguments[separator + 1 :]
    try:
        descriptor = os.open(os.path.join(cgroup, "cgroup.procs"), os.O_WRONLY)
        try:
            os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        finally:
            os.close(descriptor)
        os.execvpe(command[0], command, os.environ)
    except OSError as error:
        print(f"execution cgroup join failed: {error}", file=sys.stderr)
        return 127
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
