# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import ast
import inspect
from pathlib import Path

from devlegate import recovery_workflow
from devlegate.runtime import ServiceEngine


def test_workflow_recovery_protocol_covers_actual_owner_calls():
    tree = ast.parse(Path(recovery_workflow.__file__).read_text())
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "owner"
    }
    protocol = recovery_workflow.WorkflowRecoveryCapabilities
    declared = {
        name
        for name, value in vars(protocol).items()
        if not name.startswith("_") and inspect.isfunction(value)
    }
    assert calls == declared
    engine = object.__new__(ServiceEngine)
    for name in calls:
        expected = inspect.signature(getattr(protocol, name))
        actual = inspect.signature(getattr(engine, name))
        expected_params = list(expected.parameters.values())[1:]
        assert [(p.name, p.kind, p.default) for p in expected_params] == [
            (p.name, p.kind, p.default) for p in actual.parameters.values()
        ], name
