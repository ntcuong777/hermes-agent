"""Cursor SDK runtime display: actual thinking, honest progress, tool terminals, one event per identity.

Real AIAgent with a fake worker process. The reasoning pane gets only thinking deltas; progress goes to
the spinner callback; hidden reasoning stays hidden; what the SDK is told a tool returned is exactly
what the transcript stores.
"""

from __future__ import annotations

import json
import sys
import textwrap
from unittest.mock import patch

import pytest

import agent.cursor_runtime as cr
import run_agent


def _worker(tmp_path, body: str):
    script = tmp_path / "w.py"
    script.write_text(textwrap.dedent("""
        import json, sys
        def send(m): print(json.dumps(m), flush=True)
        def recv(): return json.loads(sys.stdin.readline())
        def event(kind, text): send({"type": "event", "turnId": tid, "kind": kind, "text": text})
        send({"type": "ready"})
        start = recv(); tid = start["turnId"]
    """) + textwrap.dedent(body))
    return [sys.executable, str(script)]


def _agent(toolsets=("file",)):
    return run_agent.AIAgent(
        api_key="stub", base_url="https://stub.invalid", provider="cursor", model="composer-2.5",
        api_mode="cursor_sdk", quiet_mode=True, skip_context_files=True, skip_memory=True,
        enabled_toolsets=list(toolsets),
    )


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "test-key-not-real")


def _run(agent, monkeypatch, command):
    monkeypatch.setattr(cr, "_worker_command", lambda: command)
    return agent.run_conversation("go")


def test_reasoning_pane_gets_only_thinking_and_progress_goes_to_the_spinner(tmp_path, monkeypatch):
    command = _worker(tmp_path, """
        event("progress", "running")
        event("thinking", "plan A")
        event("text", "hello")
        send({"type": "done", "turnId": tid, "status": "finished"})
    """)
    agent, reasoning, spinner, text = _agent(), [], [], []
    agent.reasoning_callback = reasoning.append
    agent.thinking_callback = spinner.append
    agent.stream_delta_callback = lambda t: text.append(t) if t else None
    result = _run(agent, monkeypatch, command)
    assert result["completed"] is True
    assert reasoning == ["plan A"]
    assert any("running" in s for s in spinner)
    assert spinner[-1] == ""  # the spinner is cleared once the turn produces output
    assert "".join(text) == "hello" == result["final_response"]


def test_hidden_reasoning_is_not_shown_but_the_turn_still_streams(tmp_path, monkeypatch):
    command = _worker(tmp_path, """
        event("thinking", "secret plan")
        event("text", "visible")
        send({"type": "done", "turnId": tid, "status": "finished"})
    """)
    agent, text = _agent(), []
    agent.reasoning_callback = None
    agent.stream_delta_callback = lambda t: text.append(t) if t else None
    result = _run(agent, monkeypatch, command)
    assert result["completed"] is True and "".join(text) == "visible"
    assert "secret plan" not in "".join(text)


def test_each_delta_is_delivered_once_and_equals_the_final_response(tmp_path, monkeypatch):
    command = _worker(tmp_path, """
        for part in ("ab", "cd", "ef"):
            event("text", part)
        send({"type": "done", "turnId": tid, "status": "finished"})
    """)
    agent, text = _agent(), []
    agent.stream_delta_callback = lambda t: text.append(t) if t else None
    result = _run(agent, monkeypatch, command)
    assert "".join(text) == "abcdef" == result["final_response"]


def test_the_sdk_is_told_exactly_what_the_transcript_stores(tmp_path, monkeypatch):
    note = tmp_path / "n.txt"
    note.write_text("line\n")
    command = _worker(tmp_path, f"""
        send({{"type": "tool-request", "turnId": tid, "callId": "c1", "name": "read_file",
               "args": {{"path": {json.dumps(str(note))}}}}})
        reply = recv()
        event("text", json.dumps(reply["text"]))
        send({{"type": "done", "turnId": tid, "status": "finished"}})
    """)
    result = _run(_agent(), monkeypatch, command)
    told = json.loads(result["final_response"])
    stored = next(m for m in result["messages"] if m["role"] == "tool")["content"]
    assert told == stored


def test_thinking_is_stored_once_per_round_and_not_repeated_across_rows(tmp_path, monkeypatch):
    note = tmp_path / "n.txt"
    note.write_text("x")
    command = _worker(tmp_path, f"""
        event("thinking", "first thought")
        send({{"type": "tool-request", "turnId": tid, "callId": "c1", "name": "read_file",
               "args": {{"path": {json.dumps(str(note))}}}}})
        recv()
        event("thinking", "second thought")
        event("text", "done")
        send({{"type": "done", "turnId": tid, "status": "finished"}})
    """)
    result = _run(_agent(), monkeypatch, command)
    reasoning = [m.get("reasoning") for m in result["messages"] if m["role"] == "assistant"]
    assert reasoning == ["first thought", "second thought"]


def test_an_executed_tool_gets_exactly_one_start_and_one_terminal_display(tmp_path, monkeypatch):
    note = tmp_path / "n.txt"
    note.write_text("x")
    command = _worker(tmp_path, f"""
        send({{"type": "tool-request", "turnId": tid, "callId": "c1", "name": "read_file",
               "args": {{"path": {json.dumps(str(note))}}}}})
        recv()
        send({{"type": "done", "turnId": tid, "status": "finished"}})
    """)
    agent, started, completed = _agent(), [], []
    agent.tool_start_callback = lambda *a, **k: started.append(a)
    agent.tool_complete_callback = lambda *a, **k: completed.append(a)
    _run(agent, monkeypatch, command)
    assert len(started) == 1 and len(completed) == 1
    assert started[0][0] == completed[0][0]  # same call identity


def test_a_blocked_call_shows_the_same_as_the_standard_loop_and_changes_nothing(tmp_path, monkeypatch):
    # Hermes fires no start/complete callback for a blocked call (the denial is the committed tool row);
    # the bridge runs the same executor, so it inherits that and must not invent a display of its own.
    target = tmp_path / "blocked.txt"
    command = _worker(tmp_path, f"""
        send({{"type": "tool-request", "turnId": tid, "callId": "c1", "name": "write_file",
               "args": {{"path": {json.dumps(str(target))}, "content": "x"}}}})
        recv()
        send({{"type": "done", "turnId": tid, "status": "finished"}})
    """)
    agent, started, completed = _agent(), [], []
    agent.tool_start_callback = lambda *a, **k: started.append(a)
    agent.tool_complete_callback = lambda *a, **k: completed.append(a)
    with patch("hermes_cli.plugins._dispatch_pre_tool_call_hooks", return_value=("denied", {})):
        result = _run(agent, monkeypatch, command)
    assert not target.exists()
    assert started == [] and completed == []
    assert "denied" in next(m for m in result["messages"] if m["role"] == "tool")["content"]
