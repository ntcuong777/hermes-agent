"""A child scope or a confirm spec is refused when subagent-scope's patches are absent."""

from types import SimpleNamespace

import pytest

import tools.delegate_tool as tool


def test_a_scope_bearing_route_is_not_built():
    route = SimpleNamespace(toolsets=("file",), instructions="", child_profile=None)
    with pytest.raises(ValueError, match="not applied"):
        tool._child_scope_build(route, {"role": "leaf"}, "leaf")


def test_instructions_are_not_inserted():
    with pytest.raises(ValueError, match="not applied"):
        tool._scope_child_prompt("prompt", "scout", "do the thing")


def test_a_confirm_spec_is_refused_without_asking():
    route = SimpleNamespace(confirm={"refusal": "no"})
    assert tool._confirm_ready(None, [route], 0) == "no"
