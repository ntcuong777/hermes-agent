"""delegate_task roles: a role may pin the provider as well as the model (fork patch P35).

Bug: a role's ``model`` was handed to the child alone, so a child built for ``openai-codex/gpt-6-luna`` kept the
PARENT's provider (anthropic) and sent that string to api.anthropic.com: ``HTTP 404: model: openai-codex/gpt-6-luna``.
A profile now carries ``provider``; the child gets that provider's own credentials, endpoint and wire.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from tools import delegate_roles
from tools.delegate_roles import RoleProfile
from tools.delegate_tool import _build_child_agent, _build_children, _resolve_delegation_credentials

CODEX_RUNTIME = {
    "provider": "openai-codex",
    "base_url": "https://chatgpt.com/backend-api/codex",
    "api_key": "codex-key",
    "api_mode": "codex_responses",
}


def _parent():
    parent = MagicMock()
    parent.base_url = "https://api.anthropic.com"
    parent.api_key = "parent-key"
    parent.provider = "anthropic"
    parent.api_mode = "anthropic_messages"
    parent.model = "claude-sonnet-5-5"
    parent.platform = "cli"
    parent.providers_allowed = parent.providers_ignored = parent.providers_order = parent.provider_sort = None
    parent._session_db = None
    parent._delegate_depth = 0
    parent._active_children = []
    parent._active_children_lock = threading.Lock()
    parent._print_fn = None
    parent.tool_progress_callback = None
    parent.thinking_callback = None
    parent.enabled_toolsets = ["file"]
    parent.disabled_toolsets = []
    return parent


@pytest.fixture(autouse=True)
def no_providers(monkeypatch):
    monkeypatch.setattr(delegate_roles, "_PROVIDERS", {})


def _build(profile):
    with patch("run_agent.AIAgent") as agent:
        agent.return_value = MagicMock()
        _build_child_agent(
            task_index=0, goal="do it", context=None, toolsets=None, model=None, max_iterations=10,
            task_count=1, parent_agent=_parent(), role="leaf", profile=profile,
        )
    return agent.call_args.kwargs


def test_a_role_provider_gives_the_child_that_providers_route():
    profile = RoleProfile(name="worker", toolsets=("file",), model="gpt-6-luna", provider="openai-codex")
    with patch("hermes_cli.runtime_provider.resolve_runtime_provider", return_value=CODEX_RUNTIME) as resolve:
        kwargs = _build(profile)
    assert resolve.call_args.kwargs["requested"] == "openai-codex"
    assert resolve.call_args.kwargs["target_model"] == "gpt-6-luna"
    assert (kwargs["provider"], kwargs["model"]) == ("openai-codex", "gpt-6-luna")
    assert kwargs["base_url"] == CODEX_RUNTIME["base_url"] and kwargs["api_key"] == "codex-key"
    assert kwargs["api_mode"] == "codex_responses"


def test_a_role_without_a_provider_still_inherits_the_parents_route():
    profile = RoleProfile(name="worker", toolsets=("file",), model="claude-haiku-5-5")
    kwargs = _build(profile)
    assert (kwargs["provider"], kwargs["model"]) == ("anthropic", "claude-haiku-5-5")
    assert kwargs["api_key"] == "parent-key"


def test_an_unresolvable_role_provider_refuses_the_call_and_names_it():
    profile = RoleProfile(name="worker", toolsets=("file",), model="gpt-6-luna", provider="openai-codex")
    delegate_roles.register_role_provider("t", {"worker": profile}.get, lambda: {"worker": ""})
    parent = _parent()
    creds = _resolve_delegation_credentials({}, parent)
    boom = RuntimeError("no codex login")
    with patch("run_agent.AIAgent") as agent, patch(
        "hermes_cli.runtime_provider.resolve_runtime_provider", side_effect=boom
    ):
        children, err = _build_children(
            [{"goal": "do it", "role": "worker"}], [None], creds, top_role="leaf", max_iterations=10,
            parent_agent=parent, routing_cfg={}, live_deleg_id=None, live_writers=[], task_images=None,
        )
    assert children == [] and "openai-codex" in err and "no codex login" in err
    agent.assert_not_called()
