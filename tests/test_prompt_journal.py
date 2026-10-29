# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import json
from pathlib import Path

import pytest
from _control_support import control_fixture, invoke
from runtime_helpers import run_test_iteration

import devlegate.runtime as runtime
from devlegate.worker_egress import WorkerRunResult
from devlegate.worker_prompt import worker_prompt_sha256


@pytest.mark.parametrize(
    "mode", ["sent", "delivery_failed", "mark_failed", "write_failed"]
)
def test_prompt_journal_tracks_persistence_and_delivery(tmp_path, monkeypatch, mode):
    working, config, _state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = runtime.ServiceEngine(config)
    events = []
    observed = {}

    def log(message):
        if message.startswith("worker prompt "):
            events.append(message)
            if message.startswith("worker prompt persisted:"):
                path = next((engine.state_dir / "prompts").glob("*.json"))
                observed["path"] = path
                observed["persisted_payload"] = json.loads(path.read_text())

    monkeypatch.setattr(runtime, "_log", log)
    if mode == "write_failed":
        def fail_write(*args, **kwargs):
            raise OSError("injected write failure")

        monkeypatch.setattr(runtime, "write_worker_prompt_artifact", fail_write)
    if mode == "mark_failed":
        def fail_mark(path):
            raise OSError("private secret /unrelated/path")

        monkeypatch.setattr(runtime, "mark_worker_prompt_sent", fail_mark)

    def worker(_workspace, prompt, **kwargs):
        observed["execution"] = kwargs["execution_id"]
        observed["prompt"] = prompt
        if mode != "delivery_failed":
            kwargs["prompt_delivered_handler"]()
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine._workers, "run", worker)
    if mode == "write_failed":
        with pytest.raises(OSError, match="injected write failure"):
            run_test_iteration(engine)
        assert len(events) == 1 and events[0].startswith("worker prompt constructed:")
        assert not observed
        return

    run_test_iteration(engine)
    path = observed["path"]
    execution = observed["execution"]
    digest = worker_prompt_sha256(observed["prompt"])
    assert path == engine.state_dir / "prompts" / f"{execution}.json"
    assert path.is_file()
    payload = observed["persisted_payload"]
    assert payload["execution_id"] == execution
    assert payload["prompt"] == observed["prompt"]
    assert payload["prompt_sha256"] == digest
    assert payload["delivery"] == "constructed"
    assert events[0].startswith("worker prompt constructed:")
    assert events[1] == (
        f"worker prompt persisted: execution={execution} "
        f"path={str(path)!r} sha256={digest}"
    )
    assert f"execution={execution}" in events[0] and f"sha256={digest}" in events[0]
    assert observed["prompt"] not in "\n".join(events)
    assert "private secret" not in "\n".join(events)
    assert "/unrelated/path" not in "\n".join(events)
    final_payload = json.loads(Path(path).read_text())
    assert final_payload["prompt_sha256"] == digest
    if mode == "sent":
        assert events[2:] == [
            f"worker prompt sent: execution={execution} sha256={digest}"
        ]
        assert final_payload["delivery"] == "sent"
    elif mode == "mark_failed":
        assert events[2:] == [
            "worker prompt diagnostic update failed after successful delivery: "
            f"execution={execution}"
        ]
        assert final_payload["delivery"] == "constructed"
    else:
        assert len(events) == 2
        assert final_payload["delivery"] == "constructed"
