# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import subprocess

from devlegate import git


def test_run_git_preserves_command_and_result_options(monkeypatch, tmp_path):
    captured = {}
    expected = subprocess.CompletedProcess([], 0, "output\n", "")

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(git.subprocess, "run", fake_run)

    result = git.run_git(
        tmp_path, "update-ref", "refs/heads/main", "new", "old", check=False,
        input_text="payload",
    )

    assert result is expected
    assert captured == {
        "args": (
            [
                "git",
                "-C",
                str(tmp_path),
                "update-ref",
                "refs/heads/main",
                "new",
                "old",
            ],
        ),
        "kwargs": {
            "input": "payload",
            "text": True,
            "capture_output": True,
            "check": False,
        },
    }


def test_resolve_commit_normalizes_success_and_missing_revision(tmp_path):
    calls = []

    def fake_runner(cwd, *args, check):
        calls.append((cwd, args, check))
        if args[2] == "missing^{commit}":
            return subprocess.CompletedProcess([], 128, "", "missing")
        return subprocess.CompletedProcess([], 0, " abc123 \n", "")

    assert git.resolve_commit(
        tmp_path, "HEAD", runner=fake_runner
    ) == "abc123"
    assert git.resolve_commit(
        tmp_path, "missing", runner=fake_runner
    ) is None
    assert calls == [
        (tmp_path, ("rev-parse", "--verify", "HEAD^{commit}"), False),
        (tmp_path, ("rev-parse", "--verify", "missing^{commit}"), False),
    ]


def test_commit_parents_normalizes_parent_list_and_failures(tmp_path):
    calls = []

    def fake_runner(cwd, *args, check):
        calls.append((cwd, args, check))
        if args[-1] == "missing":
            return subprocess.CompletedProcess([], 128, "", "missing")
        return subprocess.CompletedProcess([], 0, "commit parent-a parent-b\n", "")

    assert git.commit_parents(tmp_path, "commit", runner=fake_runner) == (
        "parent-a",
        "parent-b",
    )
    assert git.commit_parents(tmp_path, "missing", runner=fake_runner) is None
    assert calls == [
        (tmp_path, ("rev-list", "--parents", "-n", "1", "commit"), False),
        (tmp_path, ("rev-list", "--parents", "-n", "1", "missing"), False),
    ]
