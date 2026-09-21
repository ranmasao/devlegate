# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import pytest
from git_support import prepared_git_baselines
from resource_helpers import owned_short_state_dir
from test_cli import git_fixture


@pytest.fixture
def short_state_dir():
    with owned_short_state_dir() as path:
        yield path


__all__ = ["git_fixture", "prepared_git_baselines", "short_state_dir"]
