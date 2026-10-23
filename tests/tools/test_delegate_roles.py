"""delegate_task roles: a per-task ``role`` picks a child's toolsets, model and instructions (fork patch P24, C147).

Hermes' own tool ignores ``role`` ("capability is depth-derived") and always hands a child the parent's toolsets and
one shared model. A plugin can now register role profiles (``tools.delegate_roles``); a task naming one of them gets
that profile's toolsets (never wider than the parent's), model, reasoning effort and instructions, and the child
carries the profile's name so a plugin can grade what it does. With no provider registered nothing changes.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from tools import delegate_roles
from tools.delegate_roles import RoleProfile
from tools.delegate_tool_routes import route_for_config
from tools.delegate_tool import (
    DELEGATE_TASK_SCHEMA,
    _build_child_agent,
    _build_children,
    _build_dynamic_schema_overrides,
)

SCOUT = RoleProfile(
    name="scout",
    toolsets=("file", "knowledge"),
    model="zai/glm-5.2",
    instructions="Survey and report. Do not change anything.",
    reasoning_effort="low",
    description="read-only survey",
)
WORKER = RoleProfile(
    name="worker",
    toolsets=("file",),
    instructions="Do the task.",
    description="edits files",
)


def _parent(enabled=("file", "web")):
    parent = MagicMock()
    parent.base_url = "https://openrouter.ai/api/v1"
    parent.api_key = "***"
    parent.provider = "openrouter"
    parent.api_mode = "chat_completions"
    parent.model = "anthropic/claude-sonnet-4"
    parent.platform = "cli"
    parent.providers_allowed = parent.providers_ignored = parent.providers_order = (
        parent.provider_sort
    ) = None
    parent._session_db = None
    parent._delegate_depth = 0
    parent._active_children = []
    parent._active_children_lock = threading.Lock()
    parent._print_fn = None
    parent.tool_progress_callback = None
    parent.thinking_callback = None
    parent.enabled_toolsets = list(enabled)
    parent.disabled_toolsets = []
    return parent


@pytest.fixture(autouse=True)
def no_providers(monkeypatch):
    monkeypatch.setattr(delegate_roles, "_PROVIDERS", {})


def provide(*profiles, name="test"):
    by_name = {p.name: p for p in profiles}
    delegate_roles.register_role_provider(
        name, by_name.get, lambda: {p.name: p.description for p in profiles}
    )


def build_one(parent, profile=None, **extra):
    with patch("run_agent.AIAgent") as agent:
        agent.return_value = MagicMock()
        child = _build_child_agent(
            task_index=0,
            goal="survey the layout",
            context=None,
            toolsets=None,
            model=None,
            max_iterations=10,
            task_count=1,
            parent_agent=parent,
            role="leaf",
            profile=profile,
            **extra,
        )
    return child, agent.call_args.kwargs


def build_tasks(parent, tasks):
    routes = [route_for_config({}, parent)] * len(tasks)
    with patch("run_agent.AIAgent") as agent:
        agent.side_effect = lambda **kw: MagicMock()
        children, err = _build_children(
            tasks,
            [None] * len(tasks),
            routes,
            top_role="leaf",
            max_iterations=10,
            parent_agent=parent,
            live_deleg_id=None,
            live_writers=[],
            task_images=None,
        )
    return agent.call_args_list, children, err


# -- the registry ------------------------------------------------------------------------------------------------


def test_without_a_provider_there_are_no_roles():
    assert delegate_roles.profile_for("scout") is None
    assert delegate_roles.role_catalog() == {}


def test_a_provider_resolves_a_role_by_name_ignoring_case():
    provide(SCOUT, WORKER)
    assert delegate_roles.profile_for("scout") is SCOUT
    assert delegate_roles.profile_for(" Worker ") is WORKER


def test_no_name_and_the_old_depth_roles_are_never_profiles():
    delegate_roles.register_role_provider(
        "greedy", lambda name: RoleProfile(name=name), lambda: {"x": "x"}
    )
    for name in (None, "", "leaf", "orchestrator", "LEAF"):
        assert delegate_roles.profile_for(name) is None


def test_an_unknown_name_is_refused_with_the_declared_roles_named():
    provide(SCOUT, WORKER)
    with pytest.raises(ValueError) as caught:
        delegate_roles.profile_for("wizard")
    message = str(caught.value)
    assert "wizard" in message and "scout" in message and "worker" in message


def test_a_provider_that_fails_refuses_instead_of_spawning_unscoped():
    def broken(name):
        raise RuntimeError("roster unreadable")

    delegate_roles.register_role_provider("broken", broken, dict)
    with pytest.raises(ValueError):
        delegate_roles.profile_for("scout")


def test_registering_the_same_name_again_replaces_it():
    provide(SCOUT, name="p")
    provide(WORKER, name="p")  # a plugin reload
    assert delegate_roles.role_catalog() == {"worker": "edits files"}


def test_the_catalog_merges_providers():
    provide(SCOUT, name="a")
    provide(WORKER, name="b")
    assert delegate_roles.role_catalog() == {
        "scout": "read-only survey",
        "worker": "edits files",
    }


# -- one child ---------------------------------------------------------------------------------------------------


def test_a_role_narrows_toolsets_to_what_the_parent_has():
    _child, kwargs = build_one(_parent(enabled=("file", "web")), SCOUT)
    assert kwargs["enabled_toolsets"] == [
        "file"
    ]  # `knowledge` is not the parent's: dropped, never added


def test_without_a_role_the_child_inherits_the_parents_toolsets():
    _child, kwargs = build_one(_parent(enabled=("file", "web")))
    assert kwargs["enabled_toolsets"] == ["file", "web"]


def test_role_instructions_reach_the_childs_system_prompt():
    _child, kwargs = build_one(_parent(), SCOUT)
    prompt = kwargs["ephemeral_system_prompt"]
    assert (
        "ROLE: scout" in prompt
        and "Survey and report. Do not change anything." in prompt
    )


def test_a_child_without_a_role_has_no_role_section():
    _child, kwargs = build_one(_parent())
    assert "ROLE:" not in kwargs["ephemeral_system_prompt"]


def test_a_reasoning_config_argument_replaces_the_runtimes_instead_of_clashing_with_it():
    # `_resolve_child_runtime` always carries a `reasoning_config` (the parent's level, or delegation.reasoning_effort);
    # passing another used to raise "got multiple values for keyword argument 'reasoning_config'".
    _child, kwargs = build_one(
        _parent(), reasoning_config={"enabled": True, "effort": "low"}
    )
    assert kwargs["reasoning_config"] == {"enabled": True, "effort": "low"}


def test_a_role_with_no_effort_leaves_the_runtimes_reasoning_alone():
    _child, kwargs = build_one(_parent(), WORKER)
    assert "reasoning_config" in kwargs  # the runtime's own, unchanged


def test_the_profile_name_is_kept_on_the_child():
    child, _kwargs = build_one(_parent(), SCOUT)
    assert child._delegate_profile == "scout"
    plain, _ = build_one(_parent())
    assert plain._delegate_profile is None


@pytest.mark.parametrize("profile, expected", [(SCOUT, "scout"), (None, None)])
def test_the_start_event_names_the_profile(profile, expected):
    with patch("hermes_cli.lifecycle.invoke_hook") as hook:
        build_one(_parent(), profile)
    starts = [
        c.kwargs
        for c in hook.call_args_list
        if c.args and c.args[0] == "subagent_start"
    ]
    assert len(starts) == 1 and starts[0]["child_profile"] == expected
    assert starts[0]["child_role"] in (
        "leaf",
        "orchestrator",
    )  # the depth role is still reported as before


# -- a batch -----------------------------------------------------------------------------------------------------


def test_each_task_gets_its_own_roles_toolsets_and_model():
    provide(SCOUT, WORKER)
    parent = _parent(enabled=("file", "web"))
    calls, children, err = build_tasks(
        parent,
        [
            {"goal": "survey the repo layout", "role": "scout"},
            {"goal": "edit the one file", "role": "worker"},
        ],
    )
    assert err is None and len(children) == 2
    scout, worker = (c.kwargs for c in calls)
    assert scout["model"] == "zai/glm-5.2" and scout["enabled_toolsets"] == ["file"]
    assert worker["model"] == parent.model and worker["enabled_toolsets"] == ["file"]


def test_the_roles_effort_becomes_the_childs_reasoning_config():
    provide(SCOUT, WORKER)
    calls, _children, _err = build_tasks(
        _parent(), [{"goal": "survey the repo layout", "role": "scout"}]
    )
    assert calls[0].kwargs["reasoning_config"] == {"enabled": True, "effort": "low"}


def test_one_unknown_role_refuses_the_whole_call_before_any_child_is_built():
    provide(SCOUT, WORKER)
    calls, children, err = build_tasks(
        _parent(),
        [
            {"goal": "survey the repo layout", "role": "scout"},
            {"goal": "do something else", "role": "wizard"},
        ],
    )
    assert children == [] and "wizard" in err and "scout" in err
    assert calls == []


def test_the_old_depth_roles_still_work_beside_profiles():
    provide(SCOUT)
    calls, children, err = build_tasks(
        _parent(), [{"goal": "survey the repo layout", "role": "orchestrator"}]
    )
    assert err is None and len(children) == 1
    assert calls[0].kwargs["enabled_toolsets"] == ["file", "web"]


def test_without_a_provider_a_role_name_is_the_old_ignored_legacy():
    calls, children, err = build_tasks(
        _parent(), [{"goal": "survey the repo layout", "role": "scout"}]
    )
    assert err is None and len(children) == 1
    assert calls[0].kwargs["enabled_toolsets"] == ["file", "web"]


# -- what the model is told --------------------------------------------------------------------------------------


def _task_props():
    return _build_dynamic_schema_overrides()["parameters"]["properties"]["tasks"][
        "items"
    ]["properties"]


def test_the_schema_has_no_role_without_a_provider():
    assert "role" not in _task_props()


def test_the_schema_lists_the_declared_roles_when_there_is_a_provider():
    provide(SCOUT, WORKER)
    role = _task_props()["role"]
    assert role["enum"] == ["scout", "worker"]
    assert (
        "read-only survey" in role["description"]
        and "edits files" in role["description"]
    )


def test_the_static_schema_is_never_mutated():
    provide(SCOUT)
    _task_props()
    assert (
        "role"
        not in DELEGATE_TASK_SCHEMA["parameters"]["properties"]["tasks"]["items"][
            "properties"
        ]
    )
