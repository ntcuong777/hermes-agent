"""The marker module the lane-mirror plugin reads to know which lane features core no longer carries."""

import ast
import importlib.util

import gateway.session_lane_features as features


def test_the_marker_is_a_frozenset_of_known_ids():
    assert isinstance(features.REMOVED_FROM_CORE, frozenset)
    assert features.REMOVED_FROM_CORE <= {"repaint", "rescue", "gates", "forward", "pills", "model", "wake"}


def test_the_module_imports_nothing():
    # The plugin imports it on every start: it must never fail because an upstream name moved.
    tree = ast.parse(open(importlib.util.find_spec("gateway.session_lane_features").origin, encoding="utf-8").read())
    assert [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))] == []
