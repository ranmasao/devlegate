# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: I001
import pytest
from devlegate.execution_containment import DeterministicContainmentProvider
from git_support import prepared_git_baselines
from resource_helpers import owned_short_state_dir
from _cli_support import cli_daemon, git_fixture, plain_project_fixture
from _ipc_support import running_server


@pytest.fixture(autouse=True)
def deterministic_execution_containment(monkeypatch):
    """Keep semantic tests independent of host cgroup delegation."""
    monkeypatch.setattr(
        "devlegate.execution_containment.default_containment_provider",
        DeterministicContainmentProvider,
    )
    monkeypatch.setattr(
        "devlegate.worker_supervisor.default_containment_provider",
        DeterministicContainmentProvider,
    )


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
