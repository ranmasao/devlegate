# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

"""Publish a coverage payload without losing concurrent badge updates."""

import re
import subprocess
import sys
import tempfile
from pathlib import Path


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        capture_output=True,
    )


def badge_name(branch: str, default_branch: str) -> str | None:
    if branch == default_branch:
        return "coverage.json"
    match = re.fullmatch(r"release/([0-9]+\.[0-9]+\.[0-9]+)", branch)
    return f"coverage-{match[1]}.json" if match else None


def publish(
    repo: Path, branch: str, default_branch: str, commit: str, payload: bytes
) -> None:
    name = badge_name(branch, default_branch)
    if name is None:
        return
    # Use a separate worktree, but the checkout's authenticated remote and object store.
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory) / "badges"
        git(repo, "worktree", "add", "--detach", str(work), commit)
        try:
            for _ in range(20):
                remote = git(
                    repo, "ls-remote", "--heads", "origin", "badges"
                ).stdout.strip()
                git(repo, "fetch", "--no-tags", "origin", f"refs/heads/{branch}")
                ancestor = git(
                    repo, "merge-base", "--is-ancestor", commit, "FETCH_HEAD",
                    check=False,
                )
                if ancestor.returncode:
                    return
                if remote:
                    git(repo, "fetch", "--no-tags", "origin", "refs/heads/badges")
                    badge_head = git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()
                    git(work, "checkout", "--detach", badge_head)
                else:
                    git(work, "checkout", "--detach", commit)
                    git(work, "branch", "-D", "badge-initial", check=False)
                    git(work, "checkout", "--orphan", "badge-initial")
                    git(work, "rm", "-rf", "--ignore-unmatch", ".")
                marker = work / f".{name}.commit"
                if marker.exists():
                    previous = marker.read_text().strip()
                    ancestor = git(
                        repo, "merge-base", "--is-ancestor", previous, commit,
                        check=False,
                    )
                    if ancestor.returncode:
                        return
                (work / name).write_bytes(payload)
                marker.write_text(commit + "\n")
                git(work, "add", "-f", name, marker.name)
                diff = git(work, "diff", "--cached", "--quiet", check=False)
                if diff.returncode == 0:
                    return
                git(work, "-c", "user.name=github-actions[bot]", "-c",
                    "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                    "commit", "-m", f"Update {name}")
                pushed = git(
                    work, "push", "origin", "HEAD:refs/heads/badges", check=False
                )
                if pushed.returncode == 0:
                    return
                # A competing fast-forward is retried from the new badges head.
            raise RuntimeError("could not publish badge after 20 attempts")
        finally:
            git(repo, "worktree", "remove", "--force", str(work), check=False)


if __name__ == "__main__":
    branch, default_branch, commit, payload_path = sys.argv[1:]
    publish(Path.cwd(), branch, default_branch, commit, Path(payload_path).read_bytes())
