# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
from devlegate.cli import DevlegateError
from devlegate.runtime_locator import RuntimeLocator
from devlegate.service_diagnostics import (
    create,
    path_for,
    read,
    write,
)


def test_service_diagnostic_round_trip_replaces_atomically(tmp_path):
    locator = RuntimeLocator(tmp_path, tmp_path / "state", "state-key")
    diagnostic = create("runtime", DevlegateError("terminal failure"))

    write(locator, diagnostic)
    target = path_for(locator)
    loaded, corrupt = read(locator)

    assert not corrupt
    assert loaded == diagnostic
    assert target.is_file()
    assert list(target.parent.glob("*.tmp")) == []


def test_service_diagnostic_rejects_malformed_schema(tmp_path):
    locator = RuntimeLocator(tmp_path, tmp_path / "state", "state-key")
    target = path_for(locator)
    target.parent.mkdir(parents=True)
    target.write_text('{"schema":"wrong"}')

    loaded, corrupt = read(locator)

    assert loaded is None
    assert corrupt
