# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Fail-closed Linux evidence-only launcher; never run inside the owner process.

The owner supplies a closed launch specification over a pipe before exec. This
is a narrow filesystem/IPC boundary, complementary to execution_containment's
process-lifetime boundary. It is not yet the service's analysis dispatch path.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import platform
import stat
import sys

_MAX_SPEC = 64 * 1024
_READ_FILE = 1 << 2
_READ_DIR = 1 << 3
_EXECUTE = 1
_FS = (1 << 16) - 1  # ABI 5+; includes refer, truncate and device ioctl.
_SCRATCH = sum(1 << bit for bit in (1, 2, 3, 4, 5, 7, 8, 10, 12, 13, 14))


class IsolationError(ValueError):
    """The requested evidence-only boundary cannot be established."""


class _Ruleset(ctypes.Structure):
    _fields_ = [
        ("filesystem", ctypes.c_uint64), ("network", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    ]


class _PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed", ctypes.c_uint64), ("parent", ctypes.c_int32)]


class _Filter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32),
    ]


class _Program(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ushort), ("filters", ctypes.POINTER(_Filter))]


def _checked(result: int, label: str) -> int:
    if result < 0:
        raise IsolationError(f"{label}: {os.strerror(ctypes.get_errno())}")
    return result


def _seccomp(libc: ctypes.CDLL, machine: str) -> None:
    # Reject alternate syscall ABIs before examining syscall numbers. Denying
    # socketpair and all non-IP socket creation leaves only the inherited pipes
    # as local IPC. io_uring must not bypass these syscall checks. Landlock's
    # scoped signals and ptrace-domain checks also protect the service owner.
    if machine == "x86_64":
        architecture, socket_call = 0xC000003E, 41
        denied = (53, 101, 248, 249, 250, 304, 310, 311, 424, 425, 426, 427, 438)
    elif machine == "aarch64":
        architecture, socket_call = 0xC00000B7, 198
        denied = (117, 199, 217, 218, 219, 265, 270, 271, 424, 425, 426, 427, 438)
    else:
        raise IsolationError("evidence isolation requires x86_64 or aarch64 Linux")
    kill, allow, deny = 0x80000000, 0x7FFF0000, 0x00050000 | errno.EPERM
    instructions = [
        _Filter(0x20, 0, 0, 4),  # LD seccomp_data.arch
        _Filter(0x15, 1, 0, architecture), _Filter(0x06, 0, 0, kill),
        _Filter(0x20, 0, 0, 0),  # LD seccomp_data.nr
        _Filter(0x35, 0, 1, 0x40000000), _Filter(0x06, 0, 0, kill),
    ]
    for syscall in denied:
        instructions.extend((
            _Filter(0x15, 0, 1, syscall), _Filter(0x06, 0, 0, deny),
        ))
    instructions.extend((
        _Filter(0x15, 0, 4, socket_call),
        _Filter(0x20, 0, 0, 16),  # LD socket domain, args[0]
        _Filter(0x15, 2, 0, 2),  # AF_INET
        _Filter(0x15, 1, 0, 10),  # AF_INET6
        _Filter(0x06, 0, 0, deny),
        _Filter(0x06, 0, 0, allow),
    ))
    filters = (_Filter * len(instructions))(*instructions)
    program = _Program(len(filters), filters)
    _checked(libc.prctl(22, 2, ctypes.byref(program), 0, 0), "seccomp filter")


def confine(
    *, readonly: tuple[str, ...], executable: tuple[str, ...], scratch: str,
) -> None:
    """Irreversibly confine this launcher and all its descendants.

    Call only in a dedicated, single-threaded child after closing inherited
    source/control/credential descriptors. Runtime roots are owner-audited,
    read-only data; execute permission is granted only to listed regular files.
    An unavailable ABI or failed rule is a hard refusal, never a prompt fallback.
    """
    if not sys.platform.startswith("linux"):
        raise IsolationError("evidence isolation requires Linux Landlock ABI 6+")
    machine = platform.machine()
    if machine not in {"x86_64", "aarch64"}:
        raise IsolationError("unsupported evidence isolation architecture")
    libc = ctypes.CDLL(None, use_errno=True)
    abi = _checked(libc.syscall(444, 0, 0, 1), "Landlock ABI observation")
    if abi < 6:
        raise IsolationError("evidence isolation requires Landlock ABI 6+")
    ruleset = _Ruleset(_FS, 0, 3)  # Restrict outside-domain signals/abstract Unix IPC.
    ruleset_fd = _checked(
        libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0),
        "Landlock ruleset",
    )
    try:
        _checked(libc.prctl(38, 1, 0, 0, 0), "no_new_privs")
        for path, permission in (
            *((path, _READ_FILE | _READ_DIR) for path in readonly),
            *((path, _READ_FILE | _EXECUTE) for path in executable),
            (scratch, _SCRATCH),
        ):
            if not os.path.isabs(path) or os.path.realpath(path) != path or path == "/":
                raise IsolationError(
                    "evidence allowlist paths must be exact canonical paths"
                )
            descriptor = os.open(path, os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                mode = os.fstat(descriptor).st_mode
                if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise IsolationError("evidence allowlist contains a special file")
                if permission & _EXECUTE and not stat.S_ISREG(mode):
                    raise IsolationError("evidence executables must be regular files")
                if path == scratch and not stat.S_ISDIR(mode):
                    raise IsolationError("evidence scratch must be a directory")
                if stat.S_ISREG(mode):
                    permission &= ~_READ_DIR
                rule = _PathRule(permission, descriptor)
                _checked(
                    libc.syscall(445, ruleset_fd, 1, ctypes.byref(rule), 0),
                    "Landlock path rule",
                )
            finally:
                os.close(descriptor)
        _checked(libc.syscall(446, ruleset_fd, 0), "Landlock restriction")
    finally:
        os.close(ruleset_fd)
    _seccomp(libc, machine)


def main(arguments: list[str] | None = None) -> int:
    args = sys.argv[1:] if arguments is None else arguments
    try:
        if len(args) < 3 or args[0] != "--spec-fd" or args[2] != "--":
            raise IsolationError(
                "usage: evidence_launcher --spec-fd FD -- PROGRAM [ARGS]"
            )
        with os.fdopen(int(args[1]), "rb") as stream:
            raw = stream.read(_MAX_SPEC + 1)
        if len(raw) > _MAX_SPEC:
            raise IsolationError("evidence launch specification exceeds its limit")
        spec = json.loads(raw)
        if not isinstance(spec, dict) or set(spec) != {
            "readonly", "executable", "scratch", "environment", "pipes",
        }:
            raise IsolationError("invalid evidence launch specification")
        for key in ("readonly", "executable"):
            if not isinstance(spec[key], list) or not all(
                isinstance(path, str) for path in spec[key]
            ):
                raise IsolationError("invalid evidence runtime allowlist")
        pipes = spec["pipes"]
        if not isinstance(pipes, list) or not all(
            type(fd) is int and fd > 2 and stat.S_ISFIFO(os.fstat(fd).st_mode)
            for fd in pipes
        ):
            raise IsolationError("evidence channels must be inherited pipes")
        environment = spec["environment"]
        if not isinstance(environment, dict) or not all(
            key in {"HOME", "TMPDIR", "PATH", "LANG", "LC_ALL", "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY"} and isinstance(value, str)
            for key, value in environment.items()
        ):
            raise IsolationError("invalid evidence environment allowlist")
        # Close even unexpectedly inherited descriptors before establishing the
        # sandbox. /proc is needed only here, and is not granted to the worker.
        for name in os.listdir("/proc/self/fd"):
            fd = int(name)
            if fd > 2 and fd not in pipes:
                try:
                    os.close(fd)
                except OSError as error:
                    if error.errno != errno.EBADF:
                        raise
        for fd in (0, 1, 2):
            if not stat.S_ISFIFO(os.fstat(fd).st_mode):
                raise IsolationError("evidence standard streams must be owner pipes")
        os.environ.clear()
        os.environ.update(environment)
        os.chdir(spec["scratch"])
        confine(
            readonly=tuple(spec["readonly"]), executable=tuple(spec["executable"]),
            scratch=spec["scratch"],
        )
        os.execvpe(args[3], args[3:], dict(os.environ))
    except (IsolationError, OSError, ValueError, KeyError, IndexError) as error:
        print(f"evidence isolation refused: {error}", file=sys.stderr)
        return 126
    return 126


if __name__ == "__main__":
    raise SystemExit(main())
