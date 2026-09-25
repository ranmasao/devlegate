# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "build_distribution", Path(__file__).parents[1] / "tools/build_distribution.py"
)
GRAPH = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = GRAPH
SPEC.loader.exec_module(GRAPH)


def test_target_expansion_and_dependency_order():
    assert GRAPH.expand_targets("python") == ("wheel", "sdist")
    assert GRAPH.dependency_order(GRAPH.expand_targets("all")) == (
        "wheel",
        "sdist",
        "standalone",
        "deb",
        "full-source",
    )
    assert GRAPH.dependency_order(("deb", "standalone")) == (
        "wheel",
        "standalone",
        "deb",
    )


def test_target_parser_rejects_pip():
    with pytest.raises(SystemExit):
        GRAPH.parser().parse_args(["pip"])


def test_graph_cycle_detection(monkeypatch):
    monkeypatch.setitem(GRAPH.GRAPH, "wheel", ("standalone",))
    with pytest.raises(GRAPH.DistributionError, match="cycle"):
        GRAPH.dependency_order(("wheel",))


def test_missing_target_specific_tools(monkeypatch):
    monkeypatch.setattr(GRAPH.shutil, "which", lambda name: None)
    with pytest.raises(GRAPH.DistributionError, match="dpkg-deb"):
        GRAPH.require_tools(("dpkg-deb",))


def test_artifact_discovery_uses_actual_filename(tmp_path):
    (tmp_path / "devlegate-9.8.7-py3-none-any.whl").write_bytes(b"wheel")
    artifact = GRAPH.artifact_in(tmp_path, "devlegate-*.whl", "wheel")
    assert artifact.path.name == "devlegate-9.8.7-py3-none-any.whl"


def test_publish_replaces_only_after_candidate_is_ready(tmp_path):
    source = tmp_path / "candidate.whl"
    source.write_bytes(b"new")
    output = tmp_path / "dist"
    existing = output / source.name
    output.mkdir()
    existing.write_bytes(b"old")

    published = GRAPH.publish([source], output)

    assert published == [existing]
    assert existing.read_bytes() == b"new"
    assert not list(output.glob("*.pending-*"))


def test_run_reports_failed_stage(monkeypatch):
    def fail(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 1, stdout="stdout", stderr="stderr")

    monkeypatch.setattr(GRAPH.subprocess, "run", fail)
    with pytest.raises(GRAPH.DistributionError, match="stderr"):
        GRAPH.run(["failing-stage"])
