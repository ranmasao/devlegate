# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import io
import json
import subprocess

from devlegate.execution_workspace import ExecutionWorkspace
from devlegate.worker_supervisor import (
    WorkerSupervisor,
    _run_opencode,
)


class FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode

    def wait(self):
        return self.returncode


def run_worker(
    monkeypatch,
    stdout=b"",
    stderr=b"",
    returncode=0,
    *,
    execution_log=None,
    show_worker_output=True,
):
    process = FakeProcess(stdout, stderr, returncode)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    return process, _run_opencode(
        ["fake"],
        "prompt",
        execution_log=execution_log,
        show_worker_output=show_worker_output,
    )


def event(text):
    return (json.dumps({"type": "text", "part": {"text": text}}) + "\n").encode()


def report(outcome="completed", summary="implemented", remaining=None, questions=None):
    return (
        json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "type": "tool",
                    "tool": "devlegate_report",
                    "state": {
                        "status": "completed",
                        "input": {
                            "outcome": outcome,
                            "summary": summary,
                            "remaining": [] if remaining is None else remaining,
                            "questions": [] if questions is None else questions,
                        },
                    },
                },
            }
        )
        + "\n"
    ).encode()


def run_typed_worker(monkeypatch, tmp_path, stdout=b"", returncode=0):
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(stdout, returncode=returncode),
    )
    supervisor = WorkerSupervisor("opencode", "provider/model", "")
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)
    return supervisor.run(
        execution_id="execution-1", workspace=workspace, prompt="prompt"
    )
