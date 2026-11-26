# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_coverage_reports_exclude_vendor_and_keep_devlegate_branches(
    tmp_path: Path,
) -> None:
    # Run the real CLI with the project config in a disposable checkout.
    (tmp_path / "pyproject.toml").write_bytes((ROOT / "pyproject.toml").read_bytes())
    source = tmp_path / "src"
    modules = {
        "__init__.py": "",
        "decision.py": (
            "def choose(value):\n"
            "    if value == 1:\n"
            "        return 1\n"
            "    if value == 2:\n"
            "        return 2\n"
            "    if value == 3:\n"
            "        return 3\n"
            "    return 0\n"
        ),
        "unexecuted.py": "value = 0\n",
        "_vendor/__init__.py": "",
        "_vendor/example.py": "value = 1\n",
    }
    copies = tmp_path / "coverage-equivalent"
    copies.mkdir()
    wheel = copies / "devlegate-1.0-py3-none-any.whl"
    installed = copies / "installed"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, code in modules.items():
            member = f"devlegate/{name}"
            path = source / member
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(code, encoding="utf-8")
            archive.write(path, member)
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(installed)

    child = """
import sys
sys.path.insert(0, sys.argv[1])
from devlegate import decision
from devlegate._vendor import example
assert decision.__file__ == sys.argv[1] + '/devlegate/decision.py'
assert example.value == 1
assert decision.choose(int(sys.argv[2])) == int(sys.argv[2])
"""
    script = f"""
import subprocess
import sys
from devlegate import decision
assert decision.__file__ == {str(source / "devlegate/decision.py")!r}
for value, package in enumerate({list(map(str, (source, installed, wheel)))!r}, 1):
    subprocess.run([sys.executable, '-I', '-c', {child!r}, package, str(value)],
                   check=True)
"""
    driver = tmp_path / "driver.py"
    driver.write_text(script, encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("COVERAGE_")}
    env["PYTHONPATH"] = str(source)

    def coverage(*args: str) -> str:
        result = subprocess.run(
            [sys.executable, "-m", "coverage", *args],
            cwd=tmp_path, env=env, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    coverage("run", str(driver))
    coverage("combine")
    report = coverage("report", "-m")
    coverage("json")
    payload = json.loads((tmp_path / "coverage.json").read_text(encoding="utf-8"))
    files = payload["files"]
    expected = {
        "src/devlegate/__init__.py",
        "src/devlegate/decision.py",
        "src/devlegate/unexecuted.py",
    }
    assert files.keys() == expected
    assert {row.split()[0] for row in report.splitlines() if ".py" in row} == expected
    decision = files["src/devlegate/decision.py"]
    assert decision["executed_lines"] == [1, 2, 3, 4, 5, 6, 7]
    assert decision["executed_branches"] == [[2, 3], [2, 4], [4, 5], [4, 6], [6, 7]]
    assert decision["missing_branches"] == [[6, 8]]
    assert files["src/devlegate/unexecuted.py"]["missing_lines"] == [1]
    assert payload["totals"]["num_statements"] == 9
    assert payload["totals"]["num_branches"] == 6
