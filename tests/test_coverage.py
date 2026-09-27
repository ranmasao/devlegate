# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_coverage_reports_exclude_vendor_and_keep_devlegate_branches(
    tmp_path: Path,
) -> None:
    json_path = tmp_path / "coverage.json"
    script = f"""
import importlib
import json
from coverage import Coverage

coverage = Coverage(
    config_file={str(ROOT / "pyproject.toml")!r},
    data_file={str(tmp_path / ".coverage")!r},
)
coverage.config.parallel = False
coverage.start()
vendor = importlib.import_module("devlegate._vendor.nanoyaml")
owned = importlib.import_module("devlegate.output")
vendor.loads('"value": 1\\n')
owned.render_machine({{"value": 1}}, "json")
coverage.stop()
coverage.save()
coverage.load()
coverage.json_report(outfile={str(json_path)!r})
coverage.report()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=True,
        capture_output=True,
        text=True,
    )

    report = result.stdout
    coverage_json = json.loads(json_path.read_text(encoding="utf-8"))
    files = coverage_json["files"]
    owned_file = next(
        path for path in files if path.endswith("src/devlegate/output.py")
    )

    assert "src/devlegate/_vendor/nanoyaml" not in report
    assert all("src/devlegate/_vendor/nanoyaml" not in path for path in files)
    assert files[owned_file]["summary"]["num_branches"] > 0
    assert "covered_branches" in files[owned_file]["summary"]
    assert "missing_branches" in files[owned_file]["summary"]
