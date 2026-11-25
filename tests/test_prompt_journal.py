# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import json
import sys
from pathlib import Path

import pytest
from _control_support import control_fixture, git, invoke
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
        f"sha256={digest}"
    )
    assert f"execution={execution}" in events[0] and f"sha256={digest}" in events[0]
    assert observed["prompt"] not in "\n".join(events)
    assert "private secret" not in "\n".join(events)
    assert "/unrelated/path" not in "\n".join(events)
    final_payload = json.loads(Path(path).read_text())
    assert final_payload["prompt_sha256"] == digest
    assert final_payload["components"] == payload["components"]
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


@pytest.mark.parametrize(
    ("project", "explorer", "board"),
    [("atlas", True, False), ("boreal", False, True), (None, False, False),
     ("devlegate", True, True)],
)
def test_admitted_project_and_prepared_tools_match_adapter_stdin(
    tmp_path, monkeypatch, project, explorer, board,
):
    repo, config, state = control_fixture(tmp_path)
    manifest = repo / ".devlegate/project.md"
    manifest.parent.mkdir(exist_ok=True)
    route = '"worker":\n  - ".devlegate/worker.md"\n' if project else ""
    manifest.write_text(
        '---\n"type": "devlegate.project"\n"common":\n  - "shared.md"\n'
        f'"architect":\n  - "design.md"\n"reviewer":\n  - "reviewing.md"\n{route}---\n'
    )
    for name in ("shared", "design", "reviewing"):
        (repo / f"{name}.md").write_text(f"Private to other roles: {name}")
    guidance = None
    if project:
        guidance = f"Use {project} project conventions.\n"
        if project == "devlegate":
            guidance = (Path(__file__).resolve().parents[1] / ".devlegate/worker.md"
                        ).read_text()
        (manifest.parent / "worker.md").write_text(guidance)
    git(repo, "add", ".devlegate", "shared.md", "design.md", "reviewing.md")
    git(repo, "commit", "-m", "configure project-specific worker instructions")
    git(repo, "push", "origin", "main")
    admitted = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert invoke(repo, "control", "init", config=config).returncode == 0

    received = tmp_path / "received"
    installed = tmp_path / "installed"
    worker = tmp_path / "worker"
    worker.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\nfrom pathlib import Path\n"
        f"Path({str(received)!r}).write_bytes(sys.stdin.buffer.read())\n"
        "tools = Path(os.environ['OPENCODE_CONFIG_DIR']) / 'tools'\n"
        "names = '\\n'.join(p.name for p in tools.iterdir())\n"
        f"Path({str(installed)!r}).write_text(names)\n"
    )
    worker.chmod(0o755)
    config.write_text(config.read_text().replace(
        "OPENCODE_BIN=true", f"OPENCODE_BIN={worker}",
    ))
    monkeypatch.chdir(repo)
    messages = []
    monkeypatch.setattr(runtime, "_log", messages.append)

    def unavailable(*args, **kwargs):
        raise OSError("optional tool preparation failed")

    if not explorer:
        monkeypatch.setattr(runtime, "RevisionChannel", unavailable)
    if not board:
        monkeypatch.setattr(runtime, "BlackboardChannel", unavailable)
    run_test_iteration(runtime.ServiceEngine(config))
    delivered = received.read_bytes().decode()
    tools = set(installed.read_text().splitlines())
    assert ("## Change Explorer\n" in delivered) is explorer
    assert ("## Blackboard\n" in delivered) is board
    assert tools == {"devlegate_report.ts"} | (
        {"revisions.ts", "changes.ts", "diff.ts", "check.ts"} if explorer else set()
    ) | ({"devlegate_blackboard.ts"} if board else set())
    assert ("## Project Worker Instructions\n" in delivered) is bool(project)
    assert "Private to other roles" not in delivered
    for other_project in {"atlas", "boreal"} - {project}:
        assert f"Use {other_project} project conventions" not in delivered
    gates = ("Python 3.12+", "pytest", "./dev lint", "git diff --check", "coverage")
    for gate in gates:
        assert (gate in delivered) is (project == "devlegate")
    if guidance:
        assert guidance in delivered
    artifact = json.loads(next((state / "prompts").glob("*.json")).read_text())
    assert artifact["delivery"] == "sent"
    assert artifact["prompt"] == delivered
    assert artifact["prompt_sha256"] == worker_prompt_sha256(delivered)
    component = next(c for c in artifact["components"] if c["kind"] == "project")
    assert component["included"] is bool(project)
    assert component["source"]["commit"] == admitted
    documents = []
    if guidance:
        path = ".devlegate/worker.md"
        documents.append({
            "path": path,
            "blob": git(repo, "rev-parse", f"{admitted}:{path}").stdout.strip(),
            "sha256": worker_prompt_sha256(guidance),
        })
    assert component["source"]["documents"] == documents
    prompt_logs = "\n".join(msg for msg in messages if msg.startswith("worker prompt"))
    assert ".devlegate/worker.md" not in prompt_logs
    assert admitted not in prompt_logs
    assert admitted not in delivered
    if guidance:
        assert guidance not in prompt_logs


def test_declared_untracked_worker_instruction_prevents_launch(tmp_path, monkeypatch):
    repo, config, state = control_fixture(tmp_path)
    manifest = repo / ".devlegate/project.md"
    manifest.write_text(manifest.read_text().replace(
        '"type": "devlegate.project"\n',
        '"type": "devlegate.project"\n"worker":\n  - "local-worker.md"\n',
    ))
    git(repo, "add", ".devlegate/project.md")
    git(repo, "commit", "-m", "declare a worker document without tracking it")
    git(repo, "push", "origin", "main")
    assert invoke(repo, "control", "init", config=config).returncode == 0
    (repo / ".git/info/exclude").open("a").write("\nlocal-worker.md\n")
    (repo / "local-worker.md").write_text("Untracked host instructions")
    monkeypatch.chdir(repo)
    engine = runtime.ServiceEngine(config)
    monkeypatch.setattr(engine._workers, "run", lambda *a, **kw: pytest.fail("launch"))
    with pytest.raises(
        runtime.WorkspacePreparationBlockedError, match="missing tracked",
    ):
        run_test_iteration(engine)
    assert not (state / "prompts").exists()
