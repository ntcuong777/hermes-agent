"""Child-scope route fields of a delegation provider: toolsets (intersect-only), instructions, a profile name carried by
the child and ``subagent_start``, a provider-declared ``role`` enum, whole-call refusal before any child, a pinned
provider's own credentials, and a launch the human must approve.

Real ``delegate_task`` and real credential resolution; only child construction and run are stubbed. The provider here
is the shape a role plugin takes (it replaces the fork-only role seam of fork patches P24/P39).
"""

import json
import threading
from unittest.mock import MagicMock, Mock, patch

import pytest

from agent.delegation_provider import (
    PROVIDER_DECLARABLE_PARAMETERS, RESERVED_TASK_PARAMETERS, ROUTE_KEYS, DelegationProvider, DelegationRouteError,
)
from tools.delegate_tool_routes import resolve_task_routes, schema_extension

CODEX_RUNTIME = {
    "provider": "openai-codex", "base_url": "https://chatgpt.com/backend-api/codex", "api_key": "codex-key",
    "api_mode": "codex_responses",
}
ROLES = {
    "scout": {"toolsets": ["file", "knowledge"], "model": "zai-model", "instructions": "Survey and report.",
              "reasoning_effort": "low", "child_profile": "scout"},
    "worker": {"toolsets": ["file"], "instructions": "Do the task.", "child_profile": "worker"},
    "bare": {"child_profile": "bare"},
    "codex": {"provider": "openai-codex", "model": "gpt-6-luna", "child_profile": "codex"},
}
APPROVE, DENY = "Approve this launch", "Deny override"


class Roles(DelegationProvider):
    """A role plugin: ``role`` enum, whole-call refusal of an unknown role, a route per role."""

    seen: list

    def __init__(self):
        self.seen = []

    @property
    def name(self):
        return "roles"

    def task_parameters(self, config):
        return {
            "role": {"type": "string", "enum": sorted(ROLES), "description": "The declared role."},
            "model": {"type": "string", "description": "Optional model for this launch."},
            "provider": {"type": "string", "description": "Optional provider for this launch."},
        }

    def check_tasks(self, tasks, config):
        unknown = [t["role"] for t in tasks if t.get("role") not in ROLES]
        if unknown:
            raise DelegationRouteError(f"Unknown role {unknown[0]!r}. Declared roles: {', '.join(sorted(ROLES))}.")

    def route_task(self, task, config):
        self.seen.append(dict(task))
        route = dict(ROLES[task["role"]])
        if task.get("model") and task["model"] != route.get("model", task["parent"]["model"]):
            route["model"] = task["model"]
            route["confirm"] = {"question": f"Change model to {task['model']}?", "approve": APPROVE, "deny": DENY,
                                "refusal": "Model override was not approved; no children launched."}
        return route


def _parent(enabled=("file", "web")):
    parent = MagicMock()
    parent.base_url, parent.api_key, parent.provider = "https://api.anthropic.com", "parent-key", "anthropic"
    parent.api_mode, parent.model, parent.platform = "anthropic_messages", "claude-sonnet-5-5", "cli"
    parent.request_overrides = {}
    parent.reasoning_config = {"enabled": True, "effort": "medium"}
    parent.providers_allowed = parent.providers_ignored = parent.providers_order = parent.provider_sort = None
    parent._session_db, parent._delegate_depth, parent._active_children = None, 0, []
    parent._active_children_lock = threading.Lock()
    parent._print_fn = parent.tool_progress_callback = parent.thinking_callback = None
    parent._delegation_backend = None
    parent.enabled_toolsets, parent.disabled_toolsets = list(enabled), []
    return parent


def _spawn(tasks, *, parent=None, provider=None, cfg=None, resolve=None):
    """``(tool result, [AIAgent kwargs], [subagent_start payloads], provider)`` for a real delegate_task call."""
    provider = provider or Roles()
    hooks = []
    with (
        patch("tools.delegate_tool._load_config", return_value=cfg or {}),
        patch("tools.delegate_tool_routes._active_provider", return_value=provider),
        patch("run_agent.AIAgent", side_effect=lambda **kw: MagicMock()) as agent,
        patch("hermes_cli.lifecycle.invoke_hook", side_effect=lambda name, **kw: hooks.append((name, kw))),
        patch("tools.delegate_tool._run_batch",
              side_effect=lambda batch, background: json.dumps(
                  {"status": "built", **({"refused": batch.refused} if batch.refused else {})})),
        patch("hermes_cli.runtime_provider.resolve_runtime_provider", side_effect=resolve or Mock(return_value=CODEX_RUNTIME)),
    ):
        from tools.delegate_tool import delegate_task
        result = json.loads(delegate_task(tasks=tasks, parent_agent=parent or _parent()))
    starts = [kw for name, kw in hooks if name == "subagent_start"]
    return result, [c.kwargs for c in agent.call_args_list], starts, provider


def _task(role="worker", **extra):
    return {"goal": "survey the repo layout", "role": role, **extra}


# -- toolsets, instructions, the profile name ---------------------------------------------------------------------------


def test_route_toolsets_are_intersected_with_the_parents_never_added():
    _, (scout,), _, _ = _spawn([_task("scout")], parent=_parent(enabled=("file", "web")))
    assert scout["enabled_toolsets"] == ["file"]  # `knowledge` is not the parent's: dropped, never added


def test_a_route_without_toolsets_inherits_the_parents():
    _, (bare,), _, _ = _spawn([_task("bare")], parent=_parent(enabled=("file", "web")))
    assert bare["enabled_toolsets"] == ["file", "web"]


def test_each_task_gets_its_own_roles_toolsets_model_and_effort():
    _, (scout, worker), _, _ = _spawn([_task("scout"), _task("worker")], parent=_parent(enabled=("file", "web")))
    assert (scout["model"], scout["enabled_toolsets"]) == ("zai-model", ["file"])
    assert (worker["model"], worker["enabled_toolsets"]) == ("claude-sonnet-5-5", ["file"])  # the parent's model
    assert scout["reasoning_config"] == {"enabled": True, "effort": "low"}
    assert worker["reasoning_config"] == {"enabled": True, "effort": "medium"}  # the parent's level


def test_instructions_reach_the_childs_system_prompt_right_after_its_opening_line():
    _, (scout, bare), _, _ = _spawn([_task("scout"), _task("bare")])
    lines = scout["ephemeral_system_prompt"].split("\n")
    assert lines[0].startswith("You are a focused subagent") and lines[1] == "" and lines[2] == "ROLE: scout"
    assert lines[3] == "Survey and report."
    assert "ROLE:" not in bare["ephemeral_system_prompt"]


@pytest.mark.parametrize("role, expected", [("scout", "scout"), ("bare", "bare")])
def test_the_child_and_the_start_event_carry_the_profile_name(role, expected):
    _, _, starts, _ = _spawn([_task(role)])
    assert len(starts) == 1 and starts[0]["child_profile"] == expected
    assert starts[0]["child_role"] in ("leaf", "orchestrator")  # the depth role is reported as before


def test_a_child_without_a_profile_reports_none():
    with patch("hermes_cli.lifecycle.invoke_hook") as hook, patch("run_agent.AIAgent", return_value=MagicMock()):
        from tools.delegate_tool import _build_child_agent
        child = _build_child_agent(
            task_index=0, goal="g", context=None, toolsets=None, model=None, max_iterations=5, task_count=1,
            parent_agent=_parent())
    (start,) = [c.kwargs for c in hook.call_args_list if c.args and c.args[0] == "subagent_start"]
    assert start["child_profile"] is None and child._delegate_profile is None


def test_a_continued_child_keeps_its_own_toolsets():
    from tools.delegate_tool import _build_children
    provider, parent = Roles(), _parent(enabled=("file", "web"))
    with patch("tools.delegate_tool_routes._active_provider", return_value=provider):
        routes, _ = resolve_task_routes([_task("scout")], {}, parent)
    built = []
    with patch("tools.delegate_tool._build_child_preserving_parent_tools",
               side_effect=lambda **kw: built.append(kw) or MagicMock()):
        _build_children([_task("scout")], [None], routes, top_role="leaf", max_iterations=5, parent_agent=parent,
                        live_deleg_id=None, live_writers=[], rebuild=[{"toolsets": ["web"]}])
    assert built[0]["toolsets"] == ["web"] and built[0]["child_profile"] == "scout"


# -- the role parameter and whole-call refusal --------------------------------------------------------------------------


def test_a_provider_may_declare_role_and_the_model_fields_but_never_toolsets():
    assert PROVIDER_DECLARABLE_PARAMETERS <= RESERVED_TASK_PARAMETERS
    declared, _ = schema_extension({})  # default backend: nothing
    assert declared == {}

    class Greedy(Roles):
        def task_parameters(self, config):
            return {**super().task_parameters(config), "toolsets": {"type": "array"}, "goal": {"type": "string"}}

    with patch("tools.delegate_tool_routes._active_provider", return_value=Greedy()):
        declared, _ = schema_extension({})
    assert set(declared) == {"role", "model", "provider"}


def test_the_schema_carries_the_declared_roles_as_an_enum():
    from tools.delegate_tool import DELEGATE_TASK_SCHEMA, _build_dynamic_schema_overrides
    with patch("tools.delegate_tool_routes._active_provider", return_value=Roles()), \
            patch("tools.delegate_tool._load_config", return_value={}):
        props = _build_dynamic_schema_overrides()["parameters"]["properties"]["tasks"]["items"]["properties"]
    assert props["role"]["enum"] == sorted(ROLES) and {"model", "provider"} <= set(props)
    static = DELEGATE_TASK_SCHEMA["parameters"]["properties"]["tasks"]["items"]["properties"]
    assert not {"role", "model", "provider"} & set(static)  # the static schema is never mutated


def test_one_unknown_role_refuses_the_whole_call_before_any_child_is_built():
    result, built, starts, _ = _spawn([_task("scout"), _task("wizard")])
    assert "wizard" in result["error"] and "scout" in result["error"]
    assert built == [] and starts == []


def test_a_role_outside_the_enum_is_refused_alone_when_the_provider_does_not_check_the_call():
    class Lenient(Roles):
        def check_tasks(self, tasks, config):
            return None

    result, built, _, _ = _spawn([_task("scout"), _task("wizard")], provider=Lenient())
    (refused,) = result["refused"]
    assert refused["task_index"] == 1 and "must be one of" in refused["error"] and len(built) == 1


def test_a_check_that_raises_refuses_the_call_instead_of_letting_it_through():
    class Broken(Roles):
        def check_tasks(self, tasks, config):
            raise RuntimeError("roster unreadable")

    result, built, _, _ = _spawn([_task("scout")], provider=Broken())
    assert "roster unreadable" in result["error"] and built == []


def test_route_task_sees_the_parents_route_and_the_declared_parameters_only():
    _, _, _, provider = _spawn([_task("scout", model="zai-model")])
    (seen,) = provider.seen
    assert seen["parent"] == {"model": "claude-sonnet-5-5", "provider": "anthropic"}
    assert seen["role"] == "scout" and seen["model"] == "zai-model" and "toolsets" not in seen


@pytest.mark.parametrize("bad", [
    {"toolsets": "file"}, {"toolsets": [1]}, {"instructions": ["x"]}, {"child_profile": 3}, {"confirm": {"question": ""}},
])
def test_a_malformed_scope_field_refuses_that_task_not_the_parents_whole_set(bad):
    class Bad(Roles):
        def route_task(self, task, config):
            return {**super().route_task(task, config), **bad}

    result, built, _, _ = _spawn([_task("worker")], provider=Bad())
    assert "error" in result and built == []


def test_the_new_route_keys_are_declared():
    assert {"toolsets", "instructions", "child_profile", "confirm"} <= ROUTE_KEYS


# -- a pinned provider (fork patch P39) ---------------------------------------------------------------------------------


def test_a_route_that_pins_a_provider_gives_the_child_that_providers_route():
    resolve = Mock(return_value=CODEX_RUNTIME)
    _, (kwargs,), _, _ = _spawn([_task("codex")], resolve=resolve)
    assert resolve.call_args.kwargs["requested"] == "openai-codex"
    assert resolve.call_args.kwargs["target_model"] == "gpt-6-luna"
    assert (kwargs["provider"], kwargs["model"]) == ("openai-codex", "gpt-6-luna")
    assert kwargs["base_url"] == CODEX_RUNTIME["base_url"] and kwargs["api_key"] == "codex-key"
    assert kwargs["api_mode"] == "codex_responses"


def test_a_route_with_only_a_model_keeps_the_parents_provider_and_key():
    _, (kwargs,), _, _ = _spawn([_task("scout")])
    assert (kwargs["provider"], kwargs["model"]) == ("anthropic", "zai-model") and kwargs["api_key"] == "parent-key"


def test_an_unresolvable_pinned_provider_refuses_the_task_and_names_the_provider():
    resolve = Mock(side_effect=RuntimeError("no codex login"))
    result, built, _, _ = _spawn([_task("codex")], resolve=resolve)
    assert "openai-codex" in result["error"] and "no codex login" in result["error"] and built == []


def test_an_unknown_reasoning_effort_refuses_the_task():
    class Typo(Roles):
        def route_task(self, task, config):
            return {"reasoning_effort": "lowish"}

    result, built, _, _ = _spawn([_task("worker")], provider=Typo())
    assert "lowish" in result["error"] and built == []


# -- a launch the human must approve ------------------------------------------------------------------------------------


def _answer(choice, outcome="submitted"):
    return {"outcome": outcome, "answers": {"q0": choice}}


def _asking(reply=None, **kw):
    parent = _parent()
    parent.clarify_callback = Mock(**({"return_value": reply} if reply is not None else kw))
    return parent


def test_an_approved_launch_reaches_the_child_and_each_launch_asks_again():
    parent = _asking(_answer(APPROVE))
    task = _task("worker", model="chosen-model")
    _, (first,), _, _ = _spawn([task], parent=parent)
    _, (second,), _, _ = _spawn([task], parent=parent)
    assert first["model"] == second["model"] == "chosen-model"
    assert parent.clarify_callback.call_count == 2
    question = parent.clarify_callback.call_args.args[0][0]["question"]
    assert "chosen-model" in question
    assert first["enabled_toolsets"] == ["file"] and "Do the task." in first["ephemeral_system_prompt"]


@pytest.mark.parametrize("reply", [_answer(DENY), _answer(APPROVE, "timed_out"), _answer(APPROVE, "cancelled")])
def test_a_denied_or_incomplete_confirmation_builds_no_children(reply):
    result, built, starts, _ = _spawn([_task("worker", model="chosen")], parent=_asking(reply))
    assert built == [] and starts == [] and "not approved" in result["error"]


def test_the_default_model_needs_no_confirmation():
    parent = _asking(side_effect=AssertionError("default must not ask"))
    _, (kwargs,), _, _ = _spawn([_task("scout", model="zai-model")], parent=parent)
    assert kwargs["model"] == "zai-model"
    parent.clarify_callback.assert_not_called()


def test_yolo_without_a_human_callback_still_refuses(monkeypatch):
    monkeypatch.setenv("HERMES_YOLO", "1")
    parent = _parent()
    parent.clarify_callback = None
    result, built, _, _ = _spawn([_task("worker", model="chosen")], parent=parent)
    assert built == [] and "not approved" in result["error"]


def test_a_denied_second_launch_builds_neither_child():
    parent = _asking(side_effect=[_answer(APPROVE), _answer(DENY)])
    result, built, _, _ = _spawn([_task("worker", model="first"), _task("worker", model="second")], parent=parent)
    assert built == [] and "not approved" in result["error"]


def test_a_task_refused_by_its_route_is_never_asked_about():
    class Refuses(Roles):
        def route_task(self, task, config):
            if task["index"] == 0:
                raise DelegationRouteError("no")
            return super().route_task(task, config)

    parent = _asking(_answer(APPROVE))
    result, built, _, _ = _spawn([_task("worker", model="a"), _task("worker", model="b")], parent=parent,
                                 provider=Refuses())
    assert parent.clarify_callback.call_count == 1 and len(built) == 1 and result["refused"][0]["task_index"] == 0


def test_a_declined_confirmation_returns_no_routes_and_the_refusal():
    parent = _asking(_answer(DENY))
    with patch("tools.delegate_tool_routes._active_provider", return_value=Roles()):
        routes, err = resolve_task_routes([_task("worker", model="chosen")], {}, parent)
    assert routes == [] and "not approved" in err


def test_the_provider_check_sees_the_parents_route():
    seen = []

    class Spy(Roles):
        def check_tasks(self, tasks, config):
            seen.extend(tasks)

    with patch("tools.delegate_tool_routes._active_provider", return_value=Spy()):
        resolve_task_routes([_task("worker")], {}, _parent())
    assert seen[0]["parent"] == {"model": "claude-sonnet-5-5", "provider": "anthropic"} and seen[0]["role"] == "worker"


def test_a_restarts_rebuild_does_not_ask_again():
    parent = _asking(side_effect=AssertionError("the person approved this launch already"))
    with patch("tools.delegate_tool_routes._active_provider", return_value=Roles()):
        routes, err = resolve_task_routes([_task("worker", model="chosen")], {}, parent, confirm=False)
    assert err is None and routes[0].creds["model"] == "chosen" and routes[0].confirm


# -- the human is asked last: only about a launch that would otherwise run ---------------------------------------------


def test_a_duplicate_of_a_waiting_child_is_refused_without_asking_the_person():
    parent = _asking(side_effect=AssertionError("a refused launch must not ask"))
    waiting = [{"subagent_id": "sa-1", "goal": "survey the repo layout", "note": "waiting to resume"}]
    with patch("tools.delegate_tool_auto_resume.waiting_entries", return_value=waiting):
        result, built, _, _ = _spawn([_task("worker", model="chosen")], parent=parent)
    assert built == [] and "already has this exact goal" in result["error"]
    parent.clarify_callback.assert_not_called()


def test_only_the_launch_that_survives_the_duplicate_gate_is_asked_about():
    parent = _asking(_answer(APPROVE))
    waiting = [{"subagent_id": "sa-1", "goal": "waiting goal", "note": "waiting to resume"}]
    tasks = [{**_task("worker", model="a"), "goal": "waiting goal"}, _task("worker", model="b")]
    with patch("tools.delegate_tool_auto_resume.waiting_entries", return_value=waiting):
        result, built, _, _ = _spawn(tasks, parent=parent)
    assert len(built) == 1 and built[0]["model"] == "b" and result["refused"][0]["task_index"] == 0
    assert parent.clarify_callback.call_count == 1
    assert "b" in parent.clarify_callback.call_args.args[0][0]["question"]


def test_a_launch_over_the_oneshot_budget_is_refused_without_asking_the_person():
    parent = _asking(side_effect=AssertionError("a refused launch must not ask"))
    parent._oneshot_children_spawned = 1
    with (patch("agent.oneshot_footprint.is_single_query_session", return_value=True),
          patch("tools.delegate_tool._get_oneshot_max_children", return_value=1)):
        result, built, _, _ = _spawn([_task("worker", model="chosen")], parent=parent)
    assert built == [] and "budget" in result["error"]
    parent.clarify_callback.assert_not_called()


def test_a_denied_launch_hands_the_oneshot_budget_back():
    parent = _asking(_answer(DENY))
    parent._oneshot_children_spawned = 1
    with (patch("agent.oneshot_footprint.is_single_query_session", return_value=True),
          patch("tools.delegate_tool._get_oneshot_max_children", return_value=5)):
        result, built, _, _ = _spawn([_task("worker", model="chosen")], parent=parent)
    assert built == [] and "not approved" in result["error"]
    assert parent._oneshot_children_spawned == 1


# -- core's payload keys are core's -------------------------------------------------------------------------------------


def test_a_provider_cannot_declare_a_task_parameter_named_parent():
    class Declares(Roles):
        def task_parameters(self, config):
            return {**super().task_parameters(config), "parent": {"type": "object"}}

    with patch("tools.delegate_tool_routes._active_provider", return_value=Declares()):
        props, _ = schema_extension({})
    assert "parent" not in props and "role" in props


def test_a_task_value_never_replaces_the_core_parent_in_the_route_or_check_payload():
    seen = []

    class Spy(Roles):
        def check_tasks(self, tasks, config):
            seen.extend(tasks)

        def route_task(self, task, config):
            seen.append(task)
            return {}

    declared = {"parent": {"type": "string"}}  # a declaration the name check would refuse, forced past it
    with (patch("tools.delegate_tool_routes._active_provider", return_value=Spy()),
          patch("tools.delegate_tool_routes._declared_parameters", return_value=declared)):
        resolve_task_routes([{"goal": "g", "parent": "forged"}], {}, _parent())
    assert len(seen) == 2 and all(p["parent"] == {"model": "claude-sonnet-5-5", "provider": "anthropic"} for p in seen)
