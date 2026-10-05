# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import pytest
from git_support import prepared_git_baselines
from resource_helpers import owned_short_state_dir
from test_cli import cli_daemon, git_fixture, plain_project_fixture
from test_ipc_server import running_server

collect_ignore = [
    "test_cli.py",
    "test_control_plane.py",
    "test_daemon.py",
    "test_ipc_server.py",
]


@pytest.fixture
def short_state_dir():
    with owned_short_state_dir() as path:
        yield path


__all__ = [
    "cli_daemon",
    "git_fixture",
    "plain_project_fixture",
    "prepared_git_baselines",
    "running_server",
    "short_state_dir",
]
