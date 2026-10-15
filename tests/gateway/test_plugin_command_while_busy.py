"""A plugin command that opts in runs while the chat's agent is mid-turn (P22).

The busy fast path only knew built-in commands. Any other `/word` fell through to the interrupt path: the
agent's turn was interrupted and the command reached the model as plain chat text, never the plugin. That is
exactly when a phone needs `/tell approve`: the chat is busy. `register_command(run_while_busy=True)` runs the
handler at once and leaves the agent alone. A plugin that does not opt in behaves as before.
"""

import asyncio
import importlib.util
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.session import build_session_key

_spec = importlib.util.spec_from_file_location(
    "busy_unknown_command_helpers", "tests/gateway/test_unknown_command.py"
)
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)


def busy_runner():
    runner = helpers._make_runner()
    runner._run_agent = AsyncMock(side_effect=AssertionError("leaked to the agent"))
    agent = MagicMock()
    runner._running_agents[build_session_key(helpers._make_source())] = agent
    return runner, agent


def register(monkeypatch, name, handler, *, run_while_busy):
    from hermes_cli import plugins as plugins_mod

    entries = {
        name: {"handler": handler, "description": "d", "run_while_busy": run_while_busy}
    }
    monkeypatch.setattr(plugins_mod, "get_plugin_commands", lambda: dict(entries))
    monkeypatch.setattr(
        plugins_mod,
        "get_plugin_command_handler",
        lambda n: entries.get(n, {}).get("handler"),
    )


@pytest.fixture(autouse=True)
def runtime(monkeypatch):
    import gateway.run as gateway_run

    monkeypatch.setattr(
        gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"}
    )


@pytest.mark.asyncio
async def test_an_opted_in_command_runs_while_the_agent_works_and_leaves_it_alone(
    monkeypatch,
):
    register(monkeypatch, "tell", lambda args: f"told {args}", run_while_busy=True)
    runner, agent = busy_runner()
    result = await runner._handle_message(helpers._make_event("/tell list"))
    assert result == "told list"
    assert not agent.interrupt.called
    assert not runner._pending_messages


@pytest.mark.asyncio
async def test_telegrams_underscore_form_reaches_the_hyphenated_command(monkeypatch):
    register(
        monkeypatch,
        "tell-approve",
        lambda args: f"approved {args}",
        run_while_busy=True,
    )
    runner, agent = busy_runner()
    result = await runner._handle_message(helpers._make_event("/tell_approve once"))
    assert result == "approved once" and not agent.interrupt.called


@pytest.mark.asyncio
async def test_a_plugin_that_does_not_opt_in_behaves_as_before(monkeypatch):
    register(monkeypatch, "slow-api", lambda args: "ran", run_while_busy=False)
    runner, agent = busy_runner()
    result = await runner._handle_message(helpers._make_event("/slow-api go"))
    assert result != "ran"  # not dispatched mid-run
    assert agent.interrupt.called  # the old interrupt path, unchanged


@pytest.mark.asyncio
async def test_an_async_handler_works_while_busy(monkeypatch):
    async def handler(args):
        await asyncio.sleep(0)
        return f"async {args}"

    register(monkeypatch, "tell", handler, run_while_busy=True)
    runner, agent = busy_runner()
    assert await runner._handle_message(helpers._make_event("/tell x")) == "async x"
    assert not agent.interrupt.called


@pytest.mark.asyncio
async def test_a_sync_handler_runs_off_the_loop_thread_while_busy(monkeypatch):
    seen = []

    def handler(args):
        seen.append(threading.current_thread().name)
        return "ok"

    register(monkeypatch, "tell", handler, run_while_busy=True)
    runner, _agent = busy_runner()
    await runner._handle_message(helpers._make_event("/tell x"))
    assert seen and seen[0] != threading.main_thread().name


@pytest.mark.asyncio
async def test_a_failing_handler_answers_with_the_error_and_never_interrupts(
    monkeypatch,
):
    def handler(args):
        raise RuntimeError("socket gone")

    register(monkeypatch, "tell", handler, run_while_busy=True)
    runner, agent = busy_runner()
    result = await runner._handle_message(helpers._make_event("/tell x"))
    assert "tell" in result and "socket gone" in result
    assert not agent.interrupt.called


@pytest.mark.asyncio
async def test_a_built_in_command_still_wins_over_a_plugin_of_the_same_name(
    monkeypatch,
):
    # /status is a built-in: a plugin cannot shadow it, busy or not (register_command already refuses it).
    register(monkeypatch, "status", lambda args: "plugin status", run_while_busy=True)
    runner, _agent = busy_runner()
    result = await runner._handle_message(helpers._make_event("/status"))
    assert result != "plugin status"


@pytest.mark.asyncio
async def test_idle_dispatch_is_unchanged(monkeypatch):
    register(monkeypatch, "tell", lambda args: f"idle {args}", run_while_busy=True)
    runner = helpers._make_runner()
    runner._run_agent = AsyncMock(side_effect=AssertionError("leaked to the agent"))
    assert (
        await runner._handle_message(helpers._make_event("/tell list")) == "idle list"
    )


def test_register_command_records_the_flag_and_defaults_it_off():
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest

    mgr = PluginManager()
    ctx = PluginContext(PluginManifest(name="p", source="user"), mgr)
    ctx.register_command("tell", lambda a: a, description="t", run_while_busy=True)
    ctx.register_command("other", lambda a: a, description="t")
    assert mgr._plugin_commands["tell"]["run_while_busy"] is True
    assert mgr._plugin_commands["other"]["run_while_busy"] is False
