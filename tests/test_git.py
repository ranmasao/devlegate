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


def test_commit_parents_returns_empty_tuple_for_root_commit(tmp_path):
    def fake_runner(cwd, *args, check):
        return subprocess.CompletedProcess([], 0, "root\n", "")

    assert git.commit_parents(tmp_path, "root", runner=fake_runner) == ()


def test_resolve_commit_distinguishes_missing_revision_from_git_failure(tmp_path):
    def fake_runner(cwd, *args, check):
        if args[-1] == "missing^{commit}":
            return subprocess.CompletedProcess([], 128, "", "unknown revision")
        return subprocess.CompletedProcess([], 128, "", "fatal: repository unreadable")

    assert git.resolve_commit(tmp_path, "missing", runner=fake_runner) is None
    try:
        git.resolve_commit(tmp_path, "broken", runner=fake_runner)
    except git.GitObservationError as error:
        assert "repository unreadable" in str(error)
    else:
        raise AssertionError("Git observation failures must not look like missing refs")


def test_is_ancestor_distinguishes_negative_answer_from_git_error(tmp_path):
    calls = []

    def fake_runner(cwd, *args, check):
        calls.append((cwd, args, check))
        code = {"yes": 0, "no": 1, "broken": 128}[args[-1]]
        return subprocess.CompletedProcess([], code, "", "fatal" if code else "")

    assert git.is_ancestor(tmp_path, "base", "yes", runner=fake_runner)
    assert not git.is_ancestor(tmp_path, "base", "no", runner=fake_runner)
    try:
        git.is_ancestor(tmp_path, "base", "broken", runner=fake_runner)
    except git.GitAncestryError as error:
        assert "cannot determine" in str(error)
    else:
        raise AssertionError("Git errors must not be reported as non-ancestry")
    assert calls == [
        (tmp_path, ("merge-base", "--is-ancestor", "base", "yes"), False),
        (tmp_path, ("merge-base", "--is-ancestor", "base", "no"), False),
        (tmp_path, ("merge-base", "--is-ancestor", "base", "broken"), False),
    ]


def test_common_dir_resolves_relative_output_from_repository(tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()

    def fake_runner(cwd, *args, check):
        assert cwd == repo
        return subprocess.CompletedProcess([], 0, ".git\n", "")

    assert git.common_dir(repo, runner=fake_runner) == (repo / ".git").resolve()


def test_worktree_paths_extracts_registered_paths(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"

    def fake_runner(cwd, *args, check):
        return subprocess.CompletedProcess(
            [],
            0,
            f"worktree {first}\nHEAD abc\nbranch refs/heads/main\n\n"
            f"worktree {second}\nHEAD def\ndetached\n",
            "",
        )

    assert git.worktree_paths(tmp_path, runner=fake_runner) == (first, second)


def test_worktree_paths_rejects_malformed_output(tmp_path):
    def fake_runner(cwd, *args, check):
        return subprocess.CompletedProcess([], 0, "HEAD abc\n", "")

    try:
        git.worktree_paths(tmp_path, runner=fake_runner)
    except git.GitObservationError as error:
        assert "worktree" in str(error)
    else:
        raise AssertionError("malformed worktree output must fail closed")


def test_read_object_does_not_treat_repository_failure_as_absence(tmp_path):
    def fake_runner(cwd, *args, check):
        return subprocess.CompletedProcess([], 128, "", "fatal: not a git repository")

    try:
        git.read_object(tmp_path, "missing", runner=fake_runner)
    except git.GitObservationError as error:
        assert "not a git repository" in str(error)
    else:
        raise AssertionError("repository failures must not look like missing objects")


def test_read_path_does_not_treat_repository_failure_as_absence(tmp_path):
    def fake_runner(cwd, *args, check):
        return subprocess.CompletedProcess([], 128, "", "fatal: not a git repository")

    try:
        git.read_path(tmp_path, "HEAD", "file", runner=fake_runner)
    except git.GitObservationError as error:
        assert "not a git repository" in str(error)
    else:
        raise AssertionError("repository failures must not look like missing paths")
