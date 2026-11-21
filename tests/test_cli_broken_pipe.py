# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Real process coverage for the CLI stdout boundary."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from _cli_support import git_fixture  # noqa: F401

from devlegate import cli

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["module", "console"])
def entrypoint(request):
    if request.param == "module":
        return [sys.executable, "-m", "devlegate"]
    return [str(Path(sys.executable).parent / "devlegate")]


def environment(unbuffered=False):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    if unbuffered:
        env["PYTHONUNBUFFERED"] = "1"
    else:
        env.pop("PYTHONUNBUFFERED", None)
    return env


@pytest.mark.parametrize("unbuffered", [False, True])
@pytest.mark.parametrize("args", [["version", "--json"], ["version"]])
def test_closed_reader(entrypoint, unbuffered, args):
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        result = subprocess.run(
            [*entrypoint, *args], stdout=write_fd, stderr=subprocess.PIPE,
            env=environment(unbuffered), timeout=20,
        )
    finally:
        os.close(write_fd)
    assert result.returncode == 141
    assert result.stderr == b""


def test_status_closed_reader(entrypoint, git_fixture):  # noqa: F811
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        result = subprocess.run(
            [*entrypoint, "--env", str(git_fixture["config"]), "status", "--json"],
            stdout=write_fd, stderr=subprocess.PIPE, cwd=git_fixture["working"],
            env=environment(), timeout=20,
        )
    finally:
        os.close(write_fd)
    assert result.returncode == 141
    assert result.stderr == b""


def test_status_consuming_pipe(entrypoint, git_fixture, tmp_path):  # noqa: F811
    command = [*entrypoint, "--env", str(git_fixture["config"]), "status", "--json"]
    options = {"cwd": git_fixture["working"], "env": environment(), "timeout": 20}
    piped = subprocess.run(command, capture_output=True, **options)
    with (tmp_path / "status").open("wb") as output:
        direct = subprocess.run(
            command, stdout=output, stderr=subprocess.PIPE, **options
        )
    assert piped.returncode == direct.returncode == 0
    assert piped.stdout == (tmp_path / "status").read_bytes()
    assert piped.stderr == direct.stderr == b""


@pytest.mark.parametrize("args", [["version", "--json"], ["version"]])
def test_consuming_pipe_preserves_output(entrypoint, args, tmp_path):
    command = [*entrypoint, *args]
    piped = subprocess.run(command, capture_output=True, env=environment(), timeout=20)
    with (tmp_path / "output").open("wb") as output:
        direct = subprocess.run(
            command, stdout=output, stderr=subprocess.PIPE, env=environment(),
            timeout=20,
        )
    assert piped.returncode == direct.returncode == 0
    assert piped.stdout == (tmp_path / "output").read_bytes()
    assert piped.stderr == direct.stderr == b""


def test_command_error_visible(entrypoint, tmp_path):
    result = subprocess.run(
        [*entrypoint, "status", "--json"], cwd=tmp_path, capture_output=True,
        env={**environment(), "XDG_CONFIG_HOME": str(tmp_path)}, timeout=20,
    )
    assert result.returncode != 0
    assert b"devlegate:" in result.stderr


@pytest.mark.parametrize("error", [BrokenPipeError("stderr"), OSError("other I/O")])
def test_unrelated_io_errors_propagate(monkeypatch, error):
    def fail():
        raise error

    monkeypatch.setattr(cli, "_main", fail)
    with pytest.raises(type(error), match=str(error)):
        cli.main()
