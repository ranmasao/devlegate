# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2.

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from devlegate.log_reader import (
    LogReaderError,
    execution_id,
    execution_log,
    journal_command,
    service_unit,
    stream_journal,
)
from devlegate.operational_log import execution_log_path
from devlegate.runtime_store import SQLiteRuntimeStore


def _report(control: Path, execution: str, ticket: str = "ticket") -> None:
    payload = {
        "schema": "devlegate.execution-report.v1",
        "execution_id": execution,
        "ticket_id": ticket,
        "code_base_head": "a" * 40,
        "control_head": "b" * 40,
        "execution_branch": "devlegate/execution/ticket/" + execution,
        "execution_path": str(control / "worktrees" / ticket),
        "workspace_head": None,
        "process_returncode": 1,
        "transport_error": None,
        "transport_ok": True,
        "egress_error": None,
        "egress_ok": False,
        "worker_claim": None,
        "conclusion": "failed",
        "questions": [],
        "remaining": [],
        "reason": "worker exited with status 1",
    }
    root = control / "executions" / ticket
    root.mkdir(parents=True)
    (root / f"{execution}.json").write_text(json.dumps(payload))


def test_execution_prefix_requires_unique_report(tmp_path):
    control = tmp_path / "control"
    _report(control, "a" * 32)
    assert execution_id(control, "a" * 8) == "a" * 32
    with pytest.raises(LogReaderError, match="not found"):
        execution_id(control, "b" * 8)


def test_execution_prefix_rejects_ambiguity(tmp_path):
    control = tmp_path / "control"
    _report(control, "a" * 31 + "1", ticket="one")
    _report(control, "a" * 31 + "2", ticket="two")
    with pytest.raises(LogReaderError, match="ambiguous"):
        execution_id(control, "a" * 31)


@pytest.mark.parametrize("selector", ["", "A" * 8, "../secret", "g" * 8])
def test_execution_prefix_rejects_invalid_selectors(tmp_path, selector):
    with pytest.raises(LogReaderError, match="lowercase hexadecimal"):
        execution_id(tmp_path / "control", selector)


def test_execution_log_returns_bounded_tail(tmp_path):
    state = tmp_path / "state"
    control = tmp_path / "control"
    identifier = "a" * 32
    _report(control, identifier)
    path = execution_log_path(state, "key", identifier)
    path.parent.mkdir(parents=True)
    path.write_text("one\ntwo\nthree\n")
    resolved, content = execution_log(state, "key", control, identifier, 2)
    assert resolved == identifier
    assert list(content) == ["two\n", "three\n"]


def test_execution_log_rejects_zero_lines(tmp_path):
    identifier = "a" * 32
    control = tmp_path / "control"
    _report(control, identifier)
    with pytest.raises(LogReaderError, match="positive integer"):
        execution_log(tmp_path / "state", "key", control, identifier, 0)


def test_execution_log_reports_missing_file(tmp_path):
    identifier = "a" * 32
    control = tmp_path / "control"
    _report(control, identifier)
    with pytest.raises(LogReaderError, match="execution log not found"):
        execution_log(tmp_path / "state", "key", control, identifier, 1)


def test_service_unit_uses_persisted_authority(tmp_path):
    store = SQLiteRuntimeStore(tmp_path, "key")
    store.establish_systemd_authority(
        unit_name="persisted.service",
        state_key="key",
        env_file=tmp_path / ".env",
        repository=tmp_path,
    )
    assert service_unit(tmp_path, "key") == "persisted.service"


def test_service_unit_requires_authority(tmp_path):
    with pytest.raises(LogReaderError, match="no persisted"):
        service_unit(tmp_path, "key")


def test_journal_command_is_source_specific():
    assert journal_command("devlegate-key.service", 7, False) == [
        "journalctl",
        "--user-unit",
        "devlegate-key.service",
        "--no-pager",
        "--output=cat",
        "--lines",
        "7",
    ]


def test_journal_command_binds_follow_and_lines():
    command = journal_command("persisted.service", 4, True)
    assert command[-3:] == ["--lines", "4", "--follow"]
    assert "--follow" in command
    assert "--lines" in command


def test_journal_failure_is_reported():
    def runner(*_args, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="journal unavailable")

    with pytest.raises(LogReaderError, match="journal unavailable"):
        stream_journal("persisted.service", 10, False, runner=runner)


def test_journal_follow_binds_unit_and_lines(monkeypatch, capsys):
    calls = []

    class Process:
        stdout = iter(("record\n",))

        def wait(self):
            return 0

        def terminate(self):
            raise AssertionError("process should not be terminated")

    def popen(command, **_kwargs):
        calls.append(command)
        return Process()

    monkeypatch.setattr("devlegate.log_reader.subprocess.Popen", popen)
    assert stream_journal("persisted.service", 4, True) == 0
    assert calls == [journal_command("persisted.service", 4, True)]
    assert capsys.readouterr().out == "record\n"
