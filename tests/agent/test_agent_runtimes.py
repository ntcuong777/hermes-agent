"""Pluggable whole-turn agent runtimes (agent/agent_runtimes.py): registry, claim and dispatch."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import agent_runtimes as ar
from agent.conversation_loop import _turn_without_loop


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    monkeypatch.setattr(ar, "_RUNTIMES", dict(ar._RUNTIMES))
    monkeypatch.setattr(ar, "_discover", lambda: None)


def _turn(**over):
    return SimpleNamespace(
        user_message="hi", original_user_message="hi", messages=[{"role": "user", "content": "hi"}],
        effective_task_id="task-1", _should_review_memory=False, turn_id="turn-1", **over)


def _agent(api_mode):
    return SimpleNamespace(api_mode=api_mode)


def test_registration_rejects_bad_names_and_builtin_api_modes():
    for bad in ("", "  ", None):
        with pytest.raises(ValueError):
            ar.register_agent_runtime(bad, lambda agent, **kw: None)
    with pytest.raises(ValueError):
        ar.register_agent_runtime("codex_responses", lambda agent, **kw: None)
    with pytest.raises(TypeError):
        ar.register_agent_runtime("x_rt", "not callable")


def test_unknown_or_builtin_api_mode_falls_through_to_the_generic_loop():
    ar.register_agent_runtime("x_rt", lambda agent, **kw: {"final_response": "x"})
    for mode in ("chat_completions", "anthropic_messages", "never_registered", "", None):
        assert ar.run_registered_turn(_agent(mode), _turn()) is None
        assert _turn_without_loop(_agent(mode), _turn(), "") is None


def test_a_plugin_runtime_receives_the_whole_turn_and_its_result_is_the_turns():
    seen = {}

    def run_turn(agent, **turn):
        seen.update(agent=agent, **turn)
        return {"final_response": "from plugin", "completed": True}

    ar.register_agent_runtime("x_rt", run_turn)
    agent, s = _agent("x_rt"), _turn()
    assert ar.run_registered_turn(agent, s) == {"final_response": "from plugin", "completed": True}
    assert seen == {
        "agent": agent, "user_message": "hi", "original_user_message": "hi", "messages": s.messages,
        "effective_task_id": "task-1", "should_review_memory": False, "turn_id": "turn-1",
    }


def test_the_conversation_loop_dispatches_to_the_plugin_runtime():
    ar.register_agent_runtime("x_rt", lambda agent, **kw: {"final_response": "via loop"})
    assert _turn_without_loop(_agent("x_rt"), _turn(), "") == {"final_response": "via loop"}


def test_a_runtime_returning_none_lets_the_generic_loop_run():
    ar.register_agent_runtime("x_rt", lambda agent, **kw: None)
    assert _turn_without_loop(_agent("x_rt"), _turn(), "") is None


def test_a_runtime_failure_surfaces_to_the_caller():
    def boom(agent, **kw):
        raise RuntimeError("worker died")

    ar.register_agent_runtime("x_rt", boom)
    with pytest.raises(RuntimeError, match="worker died"):
        ar.run_registered_turn(_agent("x_rt"), _turn())


def test_the_first_matching_claim_rewrites_the_api_mode_and_others_are_untouched():
    ar.register_agent_runtime("a_rt", lambda agent, **kw: None, claims=lambda r: r.provider == "a")
    ar.register_agent_runtime("b_rt", lambda agent, **kw: None, claims=lambda r: r.provider in ("a", "b"))
    pick = lambda provider, **kw: ar.claim_runtime_api_mode(  # noqa: E731
        provider=provider, api_mode="chat_completions", model_cfg=kw.get("model_cfg"), requested_provider=provider)
    assert pick("a") == "a_rt" and pick("b") == "b_rt" and pick("c") == "chat_completions"


def test_a_claim_sees_the_requested_provider_and_config():
    seen = []
    ar.register_agent_runtime("x_rt", lambda agent, **kw: None, claims=lambda r: seen.append(r) or False)
    ar.claim_runtime_api_mode(provider="p", api_mode="codex_responses", model_cfg=None, requested_provider="alias")
    assert (seen[0].provider, seen[0].requested_provider, seen[0].api_mode, dict(seen[0].model_cfg)) == (
        "p", "alias", "codex_responses", {})


def test_a_claim_that_raises_is_ignored_not_fatal():
    def bad(route):
        raise RuntimeError("bug")

    ar.register_agent_runtime("bad_rt", lambda agent, **kw: None, claims=bad)
    ar.register_agent_runtime("ok_rt", lambda agent, **kw: None, claims=lambda r: True)
    assert ar.claim_runtime_api_mode(provider="p", api_mode="chat_completions", model_cfg={}) == "ok_rt"


def test_a_registered_runtime_is_a_valid_api_mode_everywhere_modes_are_validated():
    from agent.transports import registered_api_modes
    from hermes_cli.runtime_provider import _parse_api_mode

    assert "x_rt" not in registered_api_modes()
    ar.register_agent_runtime("x_rt", lambda agent, **kw: None)
    assert "x_rt" in registered_api_modes() and _parse_api_mode("x_rt") == "x_rt"


def test_full_tool_schemas_is_opt_in_per_runtime():
    ar.register_agent_runtime("plain_rt", lambda agent, **kw: None)
    ar.register_agent_runtime("full_rt", lambda agent, **kw: None, full_tool_schemas=True)
    assert [ar.wants_full_tool_schemas(m) for m in ("plain_rt", "full_rt", "chat_completions", "", None)] == [
        False, True, False, False, False]


def test_a_name_with_padding_is_guarded_and_keyed_as_stripped():
    with pytest.raises(ValueError):
        ar.register_agent_runtime(" codex_responses ", lambda agent, **kw: None)
    assert ar.register_agent_runtime("  padded_rt ", lambda agent, **kw: None).name == "padded_rt"
    assert ar.get_agent_runtime("padded_rt") is not None


_REAL_DISCOVER = ar._discover


def test_a_second_thread_waits_for_the_first_discovery_instead_of_missing_the_runtime(monkeypatch):
    import sys
    import threading
    import types

    monkeypatch.setattr(ar, "_discover", _REAL_DISCOVER)
    first_in, release, calls = threading.Event(), threading.Event(), []

    def list_providers():
        calls.append(1)
        if len(calls) == 1:  # the first caller is mid-discovery: the module already says "discovered"
            first_in.set()
            release.wait(timeout=10)
            ar.register_agent_runtime("late_rt", lambda agent, **kw: None)
        return []

    monkeypatch.setitem(sys.modules, "providers", types.SimpleNamespace(list_providers=list_providers))
    out = {}
    first = threading.Thread(target=lambda: ar.get_agent_runtime("late_rt"))
    first.start()
    assert first_in.wait(timeout=5)
    second = threading.Thread(target=lambda: out.update(rt=ar.get_agent_runtime("late_rt")))
    second.start()
    second.join(timeout=0.3)
    assert second.is_alive()  # waiting, not reading a half-filled registry
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)
    assert out["rt"] is not None and out["rt"].name == "late_rt"


def test_a_failing_discovery_is_logged_at_debug_and_never_raised(monkeypatch, caplog):
    import sys
    import types

    def boom():
        raise RuntimeError("plugin import blew up")

    monkeypatch.setattr(ar, "_discover", _REAL_DISCOVER)
    monkeypatch.setitem(sys.modules, "providers", types.SimpleNamespace(list_providers=boom))
    with caplog.at_level("DEBUG", logger="agent.agent_runtimes"):
        assert ar.get_agent_runtime("nothing_rt") is None
    assert "discovery failed" in caplog.text
