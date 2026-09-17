# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]


def make_dev_checkout(tmp_path: Path, *, cli: bool = True) -> Path:
    checkout = tmp_path / "devlegate"
    checkout.mkdir()
    shutil.copy2(ROOT / "dev", checkout / "dev")
    (checkout / ".venv/bin").mkdir(parents=True)
    python = checkout / ".venv/bin/python"
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    if cli:
        installed = checkout / ".venv/bin/devlegate"
        installed.write_text(
            "#!/bin/sh\n"
            "printf 'cwd=%s\\n' \"$PWD\"\n"
            "for arg do printf 'arg=%s\\n' \"$arg\"; done\n"
        )
        installed.chmod(0o755)
    return checkout


def test_dev_preserves_cwd_and_arguments(tmp_path):
    checkout = make_dev_checkout(tmp_path)
    target = tmp_path / "target project"
    target.mkdir()
    command = [
        str(checkout / "dev"),
        "run",
        "once",
        "--env",
        "file with spaces",
    ]

    result = subprocess.run(
        command,
        cwd=target,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        f"cwd={target}",
        "arg=once",
        "arg=--env",
        "arg=file with spaces",
    ]


def test_dev_explains_setup_when_cli_is_missing(tmp_path):
    checkout = make_dev_checkout(tmp_path, cli=False)
    command = [str(checkout / "dev"), "run"]

    result = subprocess.run(
        command,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "run ./dev setup first" in result.stderr
