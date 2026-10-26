# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import subprocess

import pytest

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


def test_typed_ref_operations_keep_create_cas_and_unconditional_distinct(tmp_path):
    calls = []

    def fake_runner(cwd, *args, check):
        calls.append((cwd, args, check))
        return subprocess.CompletedProcess([], 0, "", "")

    git.create_ref_if_absent(tmp_path, "refs/a", "a", runner=fake_runner)
    git.compare_and_swap_ref(tmp_path, "refs/b", "b", "old", runner=fake_runner)
    git.update_ref(tmp_path, "refs/c", "c", runner=fake_runner)

    assert calls == [
        (tmp_path, ("update-ref", "refs/a", "a", ""), False),
        (tmp_path, ("update-ref", "refs/b", "b", "old"), False),
        (tmp_path, ("update-ref", "refs/c", "c"), False),
    ]


def test_typed_transport_and_worktree_operations_preserve_effectful_trace(tmp_path):
    calls = []

    def fake_runner(cwd, *args, check, input_text=None):
        calls.append((cwd, args, check, input_text))
        return subprocess.CompletedProcess([], 0, "", "")

    git.fetch_ref(tmp_path, "origin", "main", prune=True, runner=fake_runner)
    git.push_refs(
        tmp_path,
        "origin",
        "HEAD:main",
        options=("--force-with-lease=refs/heads/main:old",),
        runner=fake_runner,
    )
    git.add_worktree(
        tmp_path, "--orphan", "-b", "control", "control", runner=fake_runner
    )
    git.remove_worktree(tmp_path, tmp_path / "control", force=True, runner=fake_runner)

    assert calls == [
        (tmp_path, ("fetch", "--prune", "origin", "main"), False, None),
        (
            tmp_path,
            (
                "push",
                "--force-with-lease=refs/heads/main:old",
                "origin",
                "HEAD:main",
            ),
            False,
            None,
        ),
        (
            tmp_path,
            ("worktree", "add", "--orphan", "-b", "control", "control"),
            False,
            None,
        ),
        (
            tmp_path,
            ("worktree", "remove", "--force", str(tmp_path / "control")),
            False,
            None,
        ),
    ]


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


def test_resolve_commit_wraps_runner_os_errors(tmp_path):
    def fake_runner(_cwd, *_args, **_kwargs):
        raise OSError("Git executable unavailable")

    with pytest.raises(git.GitObservationError, match="Git executable unavailable"):
        git.resolve_commit(tmp_path, "HEAD", runner=fake_runner)


def test_resolve_ref_commit_distinguishes_absent_and_corrupt_refs(tmp_path):
    reference = "refs/devlegate/recovery/control/evidence"

    def absent_runner(_cwd, *args, **_kwargs):
        assert args == ("show-ref", "--verify", "--quiet", reference)
        return subprocess.CompletedProcess([], 1, "", "localized missing ref")

    def corrupt_runner(_cwd, *args, **_kwargs):
        if args == ("show-ref", "--verify", "--quiet", reference):
            return subprocess.CompletedProcess([], 0, "", "")
        return subprocess.CompletedProcess([], 128, "", "fatal: bad ref")

    assert git.resolve_ref_commit(tmp_path, reference, runner=absent_runner) is None
    with pytest.raises(git.GitObservationError, match="bad ref"):
        git.resolve_ref_commit(tmp_path, reference, runner=corrupt_runner)


def test_resolve_ref_commit_refuses_peeled_or_changed_identity(tmp_path):
    reference = "refs/devlegate/recovery/control/evidence"
    raw_object = "a" * 40
    resolved_commit = "b" * 40

    def inconsistent_runner(_cwd, *args, **_kwargs):
        if args == ("show-ref", "--verify", "--quiet", reference):
            return subprocess.CompletedProcess([], 0, "", "")
        if args[:2] == ("show-ref", "--verify"):
            return subprocess.CompletedProcess(
                [], 0, f"{raw_object} {reference}\n", ""
            )
        if args[:2] == ("rev-parse", "--verify"):
            return subprocess.CompletedProcess([], 0, f"{resolved_commit}\n", "")
        raise AssertionError(args)

    with pytest.raises(git.GitObservationError, match="exact commit"):
        git.resolve_ref_commit(tmp_path, reference, runner=inconsistent_runner)


def test_resolve_object_observes_exact_ref_before_localized_revision_lookup(tmp_path):
    reference = "refs/heads/devlegate/control"
    calls = []

    def fake_runner(_cwd, *args, **_kwargs):
        calls.append(args)
        if args == ("show-ref", "--verify", "--quiet", reference):
            return subprocess.CompletedProcess([], 1, "", "lokalisierter Fehler")
        if args == ("rev-parse", "--verify", reference):
            return subprocess.CompletedProcess(
                [], 128, "", "FATALER FEHLER: Eine Revision wurde erwartet"
            )
        raise AssertionError(args)

    assert git.resolve_object(tmp_path, reference, runner=fake_runner) is None
    assert calls == [("show-ref", "--verify", "--quiet", reference)]


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [(0, True), (1, False)],
)
def test_ref_exists_uses_show_ref_status_not_localized_diagnostics(
    tmp_path, returncode, expected
):
    calls = []

    def fake_runner(cwd, *args, check):
        calls.append((cwd, args, check))
        return subprocess.CompletedProcess([], returncode, "", "localized diagnostic")

    assert git.ref_exists(
        tmp_path, "refs/heads/devlegate/control", runner=fake_runner
    ) is expected
    assert calls == [
        (
            tmp_path,
            (
                "show-ref",
                "--verify",
                "--quiet",
                "refs/heads/devlegate/control",
            ),
            False,
        )
    ]


def test_ref_exists_preserves_show_ref_observation_errors(tmp_path):
    def fake_runner(_cwd, *_args, **_kwargs):
        return subprocess.CompletedProcess([], 128, "", "localized repository error")

    with pytest.raises(git.GitObservationError, match="localized repository error"):
        git.ref_exists(
            tmp_path, "refs/heads/devlegate/control", runner=fake_runner
        )


def test_history_changes_collects_commits_and_paths_in_one_observation(tmp_path):
    calls = []
    first = "a" * 40
    second = "b" * 40

    def fake_runner(cwd, *args, check):
        calls.append((cwd, args, check))
        return subprocess.CompletedProcess(
            [],
            0,
            f"{first}\x00\nA\texecutions/T-1/one.json\n\n"
            f"{second}\x00\nM\texecutions/T-1/two.json\n",
            "",
        )

    assert git.history_changes(
        tmp_path, "HEAD", "executions/T-1", runner=fake_runner
    ) == (
        (first, ("A\texecutions/T-1/one.json",)),
        (second, ("M\texecutions/T-1/two.json",)),
    )
    assert calls == [
        (
            tmp_path,
            (
                "log",
                "--format=%H%x00",
                "--name-status",
                "--no-renames",
                "HEAD",
                "--",
                "executions/T-1",
            ),
            False,
        )
    ]


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


def test_is_ancestor_wraps_runner_os_errors(tmp_path):
    def unavailable(*_args, **_kwargs):
        raise OSError("Git executable unavailable")

    with pytest.raises(git.GitAncestryError, match="Git executable unavailable"):
        git.is_ancestor(tmp_path, "base", "head", runner=unavailable)


def test_patch_equivalence_preserves_cherry_argument_order_and_markers(tmp_path):
    original = "a" * 40
    replay = "b" * 40
    base = "c" * 40
    calls = []

    def fake_runner(cwd, *args, check):
        calls.append((cwd, args, check))
        return subprocess.CompletedProcess([], 0, f"- {replay}\n+ {original}\n", "")

    assert git.patch_equivalence(
        tmp_path, original, replay, base, runner=fake_runner
    ) == ("-", "+")
    assert calls == [
        (tmp_path, ("cherry", original, replay, base), False),
    ]


@pytest.mark.parametrize(
    "output", ["-\n", f"? {'a' * 40}\n", "- not-an-object\n"]
)
def test_patch_equivalence_rejects_malformed_output(tmp_path, output):
    def fake_runner(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, output, "")

    with pytest.raises(git.GitObservationError, match="malformed"):
        git.patch_equivalence(tmp_path, "a", "b", "c", runner=fake_runner)


def test_patch_equivalence_rejects_execution_errors(tmp_path):
    def fake_runner(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 128, "", "repository unreadable")

    with pytest.raises(git.GitObservationError, match="repository unreadable"):
        git.patch_equivalence(tmp_path, "a", "b", "c", runner=fake_runner)


def test_real_git_ancestry_ref_absence_patch_equivalence_and_cas(tmp_path):
    def command(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            text=True,
            capture_output=True,
            check=True,
        )

    command("init", "-b", "main")
    command("config", "user.name", "Test User")
    command("config", "user.email", "test@example.com")
    (tmp_path / "tracked.txt").write_text("base\n")
    command("add", "tracked.txt")
    command("commit", "-m", "base")
    base = command("rev-parse", "HEAD").stdout.strip()
    command("switch", "-c", "original")
    (tmp_path / "tracked.txt").write_text("base\nchange\n")
    command("commit", "-am", "original patch")
    original = command("rev-parse", "HEAD").stdout.strip()
    command("switch", "-c", "replay", base)
    (tmp_path / "tracked.txt").write_text("base\nchange\n")
    command("commit", "-am", "replayed patch")
    replay = command("rev-parse", "HEAD").stdout.strip()

    assert git.is_ancestor(tmp_path, base, replay)
    assert not git.is_ancestor(tmp_path, replay, original)
    assert not git.ref_exists(tmp_path, "refs/heads/missing")
    assert git.patch_equivalence(tmp_path, original, replay, base) == ("-",)

    assert git.create_ref_if_absent(tmp_path, "refs/heads/race", base).returncode == 0
    assert git.compare_and_swap_ref(
        tmp_path, "refs/heads/race", replay, original
    ).returncode != 0
    assert git.compare_and_swap_ref(
        tmp_path, "refs/heads/race", replay, base
    ).returncode == 0
    assert git.compare_and_swap_ref(
        tmp_path, "refs/heads/race", original, base
    ).returncode != 0
    assert git.resolve_ref_commit(tmp_path, "refs/heads/race") == replay


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
