#!/bin/sh
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# Run as root ONLY in a disposable Arch or Debian-family test container.
set -eu
family=$1
package=$2
case "$family" in
    arch)
        pacman -Qip "$package"
        if ! command -v git; then
            # A local install must reject the package until Git is resolved.
            if pacman -U --noconfirm "$package"; then
                exit 1
            fi
        fi
        pacman -Syu --noconfirm git
        pacman -U --noconfirm "$package"
        # Official Arch container images exclude documentation by default.
        # Use a container-local config that installs the full test payload.
        PEX_INTERPRETER=1 devlegate -c '
from pathlib import Path
config = Path("/etc/pacman.conf").read_text()
Path("/tmp/devlegate-pacman.conf").write_text("\n".join(
    line for line in config.splitlines() if not line.startswith("NoExtract")
) + "\n")
'
        pacman --config /tmp/devlegate-pacman.conf -U --noconfirm "$package"
        pacman -Qkk devlegate
        ;;
    debian)
        apt-get update -qq
        apt-get install -y --no-install-recommends "$package"
        dpkg-deb -f "$package" Depends
        dpkg --verify devlegate
        ;;
    *) exit 2 ;;
esac
for tool in python python3 pip pip3 opencode; do
    if command -v "$tool"; then
        exit 1
    fi
done
devlegate --version
# Exercise the installed application Git adapter and actual capabilities,
# accepting the target distribution's Git without an exact version predicate.
PEX_INTERPRETER=1 devlegate -c '
import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from devlegate.git import run_git
assert shutil.which("git")
if sys.argv[1] == "debian":
    # Owned Debian archives need not carry md5sums. Check the actual installed
    # bytes and modes against data.tar, in addition to dpkg --verify.
    process = subprocess.Popen(
        ["dpkg-deb", "--fsys-tarfile", sys.argv[2]], stdout=subprocess.PIPE
    )
    with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
        for member in archive:
            if member.isfile():
                installed = Path("/") / member.name
                assert installed.stat().st_mode & 0o7777 == member.mode
                assert hashlib.sha256(installed.read_bytes()).digest() == (
                    hashlib.sha256(archive.extractfile(member).read()).digest()
                )
    assert process.wait() == 0
with tempfile.TemporaryDirectory() as temporary:
    repo = Path(temporary).resolve()
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    result = run_git(repo, "rev-parse", "--show-toplevel")
    assert Path(result.stdout.strip()) == repo
    result = run_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    assert Path(result.stdout.strip()) == repo / ".git"
    assert "worktree " in run_git(repo, "worktree", "list", "--porcelain").stdout
    for command in ("add", "remove"):
        help_result = subprocess.run(
            ["git", "worktree", command, "-h"], cwd=repo,
            text=True, capture_output=True
        )
        assert "usage:" in help_result.stdout + help_result.stderr
print("installed Git-backed repository discovery and capabilities passed")
' "$family" "$package"
case "$family" in
    arch)
        pacman -R --noconfirm devlegate
        test ! -e /usr/bin/devlegate
        test ! -e /usr/share/doc/devlegate
        pacman --config /tmp/devlegate-pacman.conf -U --noconfirm "$package"
        pacman -Qkk devlegate
        ;;
    debian)
        apt-get remove -y devlegate
        test ! -e /usr/bin/devlegate
        test ! -e /usr/share/doc/devlegate
        apt-get install -y --no-install-recommends "$package"
        dpkg --verify devlegate
        ;;
esac
devlegate --version
