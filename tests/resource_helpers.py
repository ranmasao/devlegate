# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def owned_short_state_dir() -> Iterator[Path]:
    """Yield one short external state directory and always remove it."""
    path = Path(tempfile.mkdtemp(prefix="devlegate-test-state-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
