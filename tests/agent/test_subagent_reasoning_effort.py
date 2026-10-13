"""Child reasoning effort comes from launch metadata; a missing effort leaves the child default."""

from types import SimpleNamespace

import pytest

from agent.subagent_lifecycle import (
    SubagentLaunchRequest,
    SubagentLifecycleService,
    _reasoning_kwargs,
)
from hermes_constants import parse_reasoning_effort


class _Child:
    def __init__(self, ident):
        self._subagent_id = ident
        self._delegate_role = "leaf"
        self._delegate_depth = 1
        self.provider = "test"
        self.model = "test-model"


@pytest.fixture
def built_kwargs(monkeypatch):
    calls = []

    def build(**kwargs):
        calls.append(kwargs)
        return _Child(f"sa-{len(calls)}")

    def run(*_args, **_kwargs):
        return {
            "status": "completed",
            "summary": "ok",
            "api_calls": 0,
            "duration_seconds": 0,
        }

    monkeypatch.setattr("tools.delegate_tool._build_child_agent", build)
    monkeypatch.setattr("tools.delegate_tool._run_single_child", run)
    return calls


def _service():
    parent = SimpleNamespace(session_id="parent-effort", enabled_toolsets=["file"])
    return SubagentLifecycleService(lambda: parent)


def test_low_effort_metadata_becomes_parsed_reasoning_config(built_kwargs):
    _service().launch(
        SubagentLaunchRequest(goal="x", metadata={"reasoning_effort": "low"})
    )

    assert built_kwargs[0]["reasoning_config"] == parse_reasoning_effort("low")
    assert built_kwargs[0]["reasoning_config"] == {"enabled": True, "effort": "low"}


def test_missing_effort_leaves_reasoning_config_unset(built_kwargs):
    _service().launch(SubagentLaunchRequest(goal="x"))

    assert "reasoning_config" not in built_kwargs[0]


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"reasoning_effort": ""},
        {"reasoning_effort": "  "},
        {"reasoning_effort": 3},
        {"reasoning_effort": "bogus"},
    ],
)
def test_helper_passes_nothing_for_absent_or_unusable_effort(metadata):
    assert _reasoning_kwargs(metadata) == {}


def test_helper_maps_known_effort_to_config():
    assert _reasoning_kwargs({"reasoning_effort": "low"}) == {
        "reasoning_config": {"enabled": True, "effort": "low"},
    }
