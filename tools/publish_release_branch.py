# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

"""Create a release branch with its iteration badge in the first commit."""

import argparse
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path

from prepare_release_readme import prepare_readme


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


def publish(repo: Path, version: str, source: str) -> str:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("version must be X.Y.Z")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", source):
        raise ValueError("source must be a full commit ID")
    if git(repo, "rev-parse", "--verify", f"{source}^{{commit}}") != source:
        raise ValueError("source must identify a commit")
    metadata = tomllib.loads(git(repo, "show", f"{source}:pyproject.toml"))
    project_version = metadata["project"]["version"]
    if not re.fullmatch(re.escape(version) + r"(?:\.dev[0-9]+)?", project_version):
        raise ValueError("source project version does not match release version")
    ref = f"refs/heads/release/{version}"
    if git(repo, "for-each-ref", "--format=%(refname)", ref) or git(
        repo, "ls-remote", "--heads", "origin", ref
    ):
        raise ValueError(f"release branch already exists: {ref}")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory) / "release"
        git(repo, "worktree", "add", "--detach", str(work), source)
        try:
            prepare_readme(work / "README.md", version)
            git(work, "add", "README.md")
            # Always create the initial publication commit, even if already prepared.
            git(
                work, "commit", "--allow-empty", "-s", "-m",
                f"Prepare release {version}",
            )
            commit = git(work, "rev-parse", "HEAD")
            # An empty expected value permits creation only, including races where
            # another publisher creates an ancestor of our commit after the check.
            git(work, "push", f"--force-with-lease={ref}:", "origin", f"{commit}:{ref}")
            return commit
        finally:
            git(repo, "worktree", "remove", "--force", str(work))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="release version X.Y.Z")
    parser.add_argument("source", help="full source commit ID")
    args = parser.parse_args()
    print(publish(Path.cwd(), args.version, args.source))
