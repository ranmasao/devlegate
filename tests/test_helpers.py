import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def make_checkout(tmp_path: Path, launcher: str, *, cli: bool = True) -> Path:
    checkout = tmp_path / "devlegate"
    checkout.mkdir()
    shutil.copy2(ROOT / launcher, checkout / launcher)
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


@pytest.mark.parametrize("launcher", ["dev", "devlegate.sh"])
def test_launcher_preserves_cwd_and_arguments(tmp_path, launcher):
    checkout = make_checkout(tmp_path, launcher)
    target = tmp_path / "target project"
    target.mkdir()
    command = [str(checkout / launcher)]
    if launcher == "dev":
        command.append("run")
    command += ["--watch", "--env", "file with spaces"]

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
        "arg=--watch",
        "arg=--env",
        "arg=file with spaces",
    ]


@pytest.mark.parametrize("launcher", ["dev", "devlegate.sh"])
def test_launcher_explains_setup_when_cli_is_missing(tmp_path, launcher):
    checkout = make_checkout(tmp_path, launcher, cli=False)
    command = [str(checkout / launcher)]
    if launcher == "dev":
        command.append("run")

    result = subprocess.run(
        command,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "run ./dev setup first" in result.stderr
