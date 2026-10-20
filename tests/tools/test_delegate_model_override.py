"""Per-task model changes need fresh, explicit human consent before any child is built."""

from unittest.mock import Mock

import pytest

from tests.tools.test_delegate_roles import _parent, build_tasks
from tools import delegate_roles
from tools.delegate_roles import RoleProfile


@pytest.fixture(autouse=True)
def scoped_role(monkeypatch):
    monkeypatch.setattr(delegate_roles, "_PROVIDERS", {})
    role = RoleProfile(
        name="worker",
        toolsets=("file",),
        model="role-default",
        instructions="Keep scope.",
    )
    delegate_roles.register_role_provider(
        "fixture",
        lambda name: role if name == "worker" else None,
        lambda: {"worker": "Scoped worker"},
    )


def _task(**overrides):
    return {"goal": "Check a hard proof", "role": "worker", **overrides}


def test_approved_model_override_reaches_child_and_each_launch_asks_again():
    parent = _parent()
    parent.clarify_callback = Mock(
        return_value={
            "outcome": "submitted",
            "answers": {"q0": "Approve this launch"},
        }
    )
    task = _task(model="chosen-model", model_rationale="Needs a larger context window")
    first, children, error = build_tasks(parent, [task])
    second, _, second_error = build_tasks(parent, [task])
    assert error is None and second_error is None and len(children) == 1
    assert first[0].kwargs["model"] == "chosen-model"
    assert second[0].kwargs["model"] == "chosen-model"
    assert parent.clarify_callback.call_count == 2
    question = parent.clarify_callback.call_args.args[0][0]["question"]
    assert "role-default" in question and "chosen-model" in question
    assert (
        "Needs a larger context window" in question and "Check a hard proof" in question
    )
    assert first[0].kwargs["enabled_toolsets"] == ["file"]
    assert "Keep scope." in first[0].kwargs["ephemeral_system_prompt"]


@pytest.mark.parametrize(
    "reply",
    [
        {"outcome": "submitted", "answers": {"q0": "Deny override"}},
        {"outcome": "timed_out", "answers": {}},
        {"outcome": "cancelled", "answers": {"q0": "Approve this launch"}},
    ],
)
def test_denial_or_incomplete_confirmation_builds_no_children(reply):
    parent = _parent()
    parent.clarify_callback = Mock(return_value=reply)
    calls, children, error = build_tasks(
        parent, [_task(model="chosen", model_rationale="Needed")]
    )
    assert calls == [] and children == []
    assert "not approved" in error


def test_default_model_needs_no_confirmation():
    parent = _parent()
    parent.clarify_callback = Mock(side_effect=AssertionError("default must not ask"))
    calls, _, error = build_tasks(parent, [_task(model="role-default")])
    assert error is None and calls[0].kwargs["model"] == "role-default"
    parent.clarify_callback.assert_not_called()


def test_override_without_rationale_is_refused_before_prompt_or_build():
    parent = _parent()
    parent.clarify_callback = Mock()
    calls, children, error = build_tasks(parent, [_task(model="chosen")])
    assert calls == [] and children == []
    assert "model_rationale" in error
    parent.clarify_callback.assert_not_called()


def test_yolo_without_human_callback_still_refuses_model_override(monkeypatch):
    monkeypatch.setenv("HERMES_YOLO", "1")
    parent = _parent()
    parent.clarify_callback = None
    calls, children, error = build_tasks(
        parent, [_task(model="chosen", model_rationale="Needed")]
    )
    assert calls == [] and children == [] and "not approved" in error


def test_denied_second_override_builds_neither_child():
    parent = _parent()
    parent.clarify_callback = Mock(
        side_effect=[
            {"outcome": "submitted", "answers": {"q0": "Approve this launch"}},
            {"outcome": "submitted", "answers": {"q0": "Deny override"}},
        ]
    )
    calls, children, error = build_tasks(
        parent,
        [
            _task(model="first", model_rationale="First reason"),
            _task(model="second", model_rationale="Second reason"),
        ],
    )
    assert calls == [] and children == [] and "not approved" in error


def test_registry_dispatch_preserves_approved_provider_and_model(monkeypatch):
    from unittest.mock import MagicMock
    from tools.registry import registry
    import tools.delegate_tool as delegation

    parent = _parent()
    parent.clarify_callback = Mock(
        return_value={
            "outcome": "submitted",
            "answers": {"q0": "Approve this launch"},
        }
    )
    child = MagicMock()
    construct = Mock(return_value=child)
    resolve = Mock(
        return_value={
            "provider": "custom",
            "base_url": "https://fixture.invalid/v1",
            "api_key": "fixture-only",
            "api_mode": "chat_completions",
        }
    )
    monkeypatch.setattr("run_agent.AIAgent", construct)
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", resolve)
    monkeypatch.setattr(
        delegation, "_run_batch", lambda *_a, **_kw: '{"status":"fixture-built"}'
    )
    result = registry.dispatch(
        "delegate_task",
        {
            "tasks": [
                _task(
                    model="chosen",
                    provider="custom",
                    model_rationale="Needs this provider's context window",
                )
            ]
        },
        parent_agent=parent,
    )
    assert result == '{"status":"fixture-built"}'
    assert parent.clarify_callback.call_count == 1
    assert (
        construct.call_args.kwargs["model"],
        construct.call_args.kwargs["provider"],
    ) == ("chosen", "custom")
    assert resolve.call_args.kwargs["requested"] == "custom"
    assert construct.call_args.kwargs["base_url"] == "https://fixture.invalid/v1"
