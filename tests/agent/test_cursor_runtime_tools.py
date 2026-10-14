"""Cursor SDK runtime bridges EVERY enabled tool through the live executor, with its full policy.

Each case drives a real AIAgent (cursor_sdk mode) with a fake worker process, so guardrails,
plugin hooks, argument rewrites, result transforms and agent-context tools run exactly as on the
standard loop. The worker never calls a registry tool: if a call skipped the executor, these fail.
"""

from __future__ import annotations

import json
import sys
import textwrap
from unittest.mock import patch

import pytest

import agent.cursor_runtime as cr
import run_agent


def _worker_calling(tmp_path, name, args):
    """Fake worker: one tool request, then finish; the host's reply is echoed as final text."""
    script = tmp_path / "w.py"
    script.write_text(textwrap.dedent(f"""
        import json, sys
        def send(m): print(json.dumps(m), flush=True)
        def recv(): return json.loads(sys.stdin.readline())
        send({{"type": "ready"}})
        start = recv(); tid = start["turnId"]
        send({{"type": "tool-request", "turnId": tid, "callId": "c1", "name": {name!r}, "args": {json.dumps(args)}}})
        reply = recv()
        send({{"type": "event", "turnId": tid, "kind": "text", "text": json.dumps(reply)}})
        send({{"type": "done", "turnId": tid, "status": "finished"}})
    """))
    return [sys.executable, str(script)]


def _agent(toolsets):
    return run_agent.AIAgent(
        api_key="stub", base_url="https://stub.invalid", provider="cursor", model="composer-2.5",
        api_mode="cursor_sdk", quiet_mode=True, skip_context_files=True, skip_memory=True,
        enabled_toolsets=toolsets,
    )


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "test-key-not-real")


def _run(agent, monkeypatch, tmp_path, name, args):
    command = _worker_calling(tmp_path, name, args)
    monkeypatch.setattr(cr, "_worker_command", lambda: command)
    result = agent.run_conversation("do it")
    reply = json.loads(result["final_response"]) if result["completed"] else None
    return result, reply


def test_the_offered_tools_are_exactly_the_agents_active_schemas():
    for toolsets in (["file"], ["file", "terminal"], ["todo"]):
        agent = _agent(toolsets)
        offered = {t["name"] for t in cr.advertised_tools(agent)}
        assert offered == set(agent.valid_tool_names)
        assert offered, toolsets


def test_a_blocked_write_causes_no_mutation_and_reports_an_error(tmp_path, monkeypatch):
    target = tmp_path / "blocked.txt"
    agent = _agent(["file"])
    with patch("hermes_cli.plugins._dispatch_pre_tool_call_hooks",
               return_value=("denied by policy", {})):
        result, reply = _run(agent, monkeypatch, tmp_path, "write_file",
                             {"path": str(target), "content": "x"})
    assert not target.exists()
    assert reply["isError"] is True and "denied by policy" in reply["text"]
    assert [m["role"] for m in result["messages"]][-3:] == ["assistant", "tool", "assistant"]


def test_an_argument_rewrite_applies_once_and_the_rewritten_call_runs(tmp_path, monkeypatch):
    asked, rewritten = tmp_path / "asked.txt", tmp_path / "rewritten.txt"
    seen = []

    def hook(name, args, **kwargs):
        seen.append((name, dict(args)))
        return None, {**args, "path": str(rewritten)}

    agent = _agent(["file"])
    with patch("hermes_cli.plugins._dispatch_pre_tool_call_hooks", side_effect=hook):
        _run(agent, monkeypatch, tmp_path, "write_file", {"path": str(asked), "content": "hello"})
    assert [name for name, _ in seen] == ["write_file"]  # the hook ran once for the one call
    assert rewritten.read_text() == "hello"
    assert not asked.exists()


def test_a_result_transform_applies_once_to_what_the_worker_and_transcript_see(tmp_path, monkeypatch):
    note = tmp_path / "note.txt"
    note.write_text("body\n")
    calls = []

    def invoke(hook_name, **kwargs):
        if hook_name != "transform_tool_result":
            return []
        calls.append(kwargs["tool_name"])
        return ["TRANSFORMED " + kwargs["result"]]

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", invoke)
    monkeypatch.setattr("hermes_cli.plugins.has_hook", lambda name: name == "transform_tool_result")
    agent = _agent(["file"])
    result, reply = _run(agent, monkeypatch, tmp_path, "read_file", {"path": str(note)})
    assert calls == ["read_file"]
    assert reply["text"].count("TRANSFORMED") == 1
    tool_row = next(m for m in result["messages"] if m["role"] == "tool")
    assert tool_row["content"].count("TRANSFORMED") == 1


def test_an_agent_context_tool_updates_the_live_agent_state(tmp_path, monkeypatch):
    # With a lone toolset Hermes defers todo_list behind its tool_call bridge; the bridge is offered and
    # forwarded like any tool, and the underlying tool still runs against the live agent.
    agent = _agent(["todo"])
    assert "tool_call" in agent.valid_tool_names
    assert not agent._todo_store.has_items()
    result, reply = _run(agent, monkeypatch, tmp_path, "tool_call", {
        "name": "todo_list",
        "arguments": {"todos": [{"id": "1", "content": "ship it", "status": "pending"}]}})
    assert result["completed"] is True and reply["isError"] is False
    assert agent._todo_store.has_items()


def _manual_approvals(monkeypatch, answer, asked):
    """Real approval gate in manual mode; the CLI-style per-thread callback answers and records."""
    from tools import approval as approval_mod
    from tools import approval_context
    from tools.terminal_tool import set_approval_callback

    for name in ("HERMES_YOLO_MODE", "HERMES_CRON_SESSION", "HERMES_GATEWAY_SESSION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HERMES_INTERACTIVE", "1")
    monkeypatch.setattr(approval_mod, "_YOLO_MODE_FROZEN", False)
    monkeypatch.setattr(approval_context, "_get_approval_config", lambda: {"mode": "manual", "timeout": 60})

    def callback(command, description, **kwargs):
        asked.append(command)
        return answer

    set_approval_callback(callback)
    monkeypatch.setattr(approval_context, "_no_user_can_answer", lambda: False, raising=False)
    return lambda: set_approval_callback(None)


def _victim(tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep")
    return victim


def test_a_denied_dangerous_command_changes_nothing(tmp_path, monkeypatch):
    victim, asked = _victim(tmp_path), []
    undo = _manual_approvals(monkeypatch, "deny", asked)
    try:
        agent = _agent(["file", "terminal"])
        result, reply = _run(agent, monkeypatch, tmp_path, "terminal", {"command": f"rm -rf {victim}"})
    finally:
        undo()
    assert asked == [f"rm -rf {victim}"]  # the real approval prompt ran, once, on the agent thread
    assert (victim / "keep.txt").read_text() == "keep"
    assert reply["isError"] is True and result["completed"] is True


def test_an_approved_dangerous_command_runs_so_the_denial_above_is_the_gate(tmp_path, monkeypatch):
    victim, asked = _victim(tmp_path), []
    undo = _manual_approvals(monkeypatch, "once", asked)
    try:
        agent = _agent(["file", "terminal"])
        _run(agent, monkeypatch, tmp_path, "terminal", {"command": f"rm -rf {victim}"})
    finally:
        undo()
    assert asked == [f"rm -rf {victim}"]
    assert not victim.exists()
