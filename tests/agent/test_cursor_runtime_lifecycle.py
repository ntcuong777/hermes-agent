"""Cursor SDK runtime lifecycle: stop, death, hostile messages, write failures, fresh state per turn.

Uses the lightweight FakeAgent from test_cursor_runtime plus fake worker processes. Effects may be
unknown after an interruption, so nothing here asserts exactly-once across a crash.
"""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

import agent.cursor_runtime as cr
from tests.agent.test_cursor_runtime import FakeAgent, _worker


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "test-key-not-real")
    monkeypatch.setattr(cr, "stage_tool_call_message",
                        lambda agent, assistant_message, finish_reason, messages:
                        (agent._build_assistant_message(assistant_message, finish_reason), False))
    monkeypatch.setattr(cr, "_CANCEL_GRACE_SECONDS", 0.4)


def _turn(agent, monkeypatch, command, messages=None, user="go"):
    monkeypatch.setattr(cr, "_worker_command", lambda: command)
    rows = messages if messages is not None else [{"role": "user", "content": user}]
    return cr.run_cursor_sdk_turn(agent, user_message=user, original_user_message=user,
                                  messages=rows, effective_task_id="t")


def test_stop_during_tool_execution_runs_nothing_further_and_keeps_the_tool_row(tmp_path, monkeypatch):
    command = _worker(tmp_path, """
        send({"type": "tool-request", "turnId": tid, "callId": "c1", "name": "read_file", "args": {}})
        recv()
        send({"type": "tool-request", "turnId": tid, "callId": "c2", "name": "read_file", "args": {}})
        cancel = recv()
        assert cancel["type"] == "cancel"
        send({"type": "done", "turnId": tid, "status": "cancelled"})
    """)

    def execute(agent, assistant_message, messages):
        FakeAgent._default_execute(agent, assistant_message, messages)
        agent._interrupt_requested = True  # the user pressed stop while the tool ran

    agent = FakeAgent(execute=execute)
    result = _turn(agent, monkeypatch, command)
    assert result["interrupted"] is True and result["completed"] is False
    assert len(agent.executed) == 1  # the second request never ran
    assert [m["role"] for m in result["messages"]] == ["user", "assistant", "tool"]


def test_a_request_that_races_a_stop_is_not_executed(tmp_path, monkeypatch):
    command = _worker(tmp_path, """
        send({"type": "event", "turnId": tid, "kind": "text", "text": "x"})
        send({"type": "tool-request", "turnId": tid, "callId": "c1", "name": "read_file", "args": {}})
        recv(); recv()
    """)
    agent = FakeAgent()
    original = agent._fire_stream_delta

    def stop_after_text(text):
        original(text)
        agent._interrupt_requested = True

    agent._fire_stream_delta = stop_after_text
    result = _turn(agent, monkeypatch, command)
    assert agent.executed == []
    assert result["interrupted"] is True


def test_a_duplicate_call_id_is_refused_without_a_second_execution(tmp_path, monkeypatch):
    command = _worker(tmp_path, """
        for _ in range(2):
            send({"type": "tool-request", "turnId": tid, "callId": "c1", "name": "read_file", "args": {}})
        first = recv(); second = recv()
        assert first["isError"] is False and second["isError"] is True
        send({"type": "done", "turnId": tid, "status": "finished"})
    """)
    agent = FakeAgent()
    result = _turn(agent, monkeypatch, command)
    assert result["completed"] is True
    assert len(agent.executed) == 1


def test_garbage_and_foreign_turn_messages_are_ignored(tmp_path, monkeypatch):
    command = _worker(tmp_path, """
        print("not json at all", flush=True)
        print("[1, 2]", flush=True)
        send({"type": "event", "turnId": "someone-else", "kind": "text", "text": "LEAK"})
        send({"type": "tool-request", "turnId": "someone-else", "callId": "c1", "name": "read_file", "args": {}})
        send({"type": "event", "turnId": tid, "kind": "text", "text": "ok"})
        send({"type": "done", "turnId": tid, "status": "finished"})
    """)
    agent = FakeAgent()
    result = _turn(agent, monkeypatch, command)
    assert result["final_response"] == "ok" and agent.executed == []


def test_an_oversized_worker_line_ends_the_turn_and_is_not_buffered_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "MAX_WORKER_LINE_CHARS", 1000)
    command = _worker(tmp_path, """
        print("x" * 5000, flush=True)
        import time; time.sleep(30)
    """)
    result = _turn(FakeAgent(), monkeypatch, command)
    assert result["completed"] is False and "oversized" in result["error"]


def test_a_worker_that_ignores_cancel_is_killed_within_bounds(tmp_path, monkeypatch):
    command = _worker(tmp_path, """
        import time
        send({"type": "event", "turnId": tid, "kind": "text", "text": "x"})
        time.sleep(60)
    """)
    agent = FakeAgent()
    original = agent._fire_stream_delta
    agent._fire_stream_delta = lambda t: (original(t), setattr(agent, "_interrupt_requested", True))
    monkeypatch.setattr(cr, "_CANCEL_GRACE_SECONDS", 0.3)
    started = time.monotonic()
    result = _turn(agent, monkeypatch, command)
    assert time.monotonic() - started < 10
    assert result["interrupted"] is True


def test_the_turn_deadline_ends_a_silent_worker(tmp_path, monkeypatch):
    command = _worker(tmp_path, "import time; time.sleep(60)")
    monkeypatch.setattr(cr, "_turn_timeout", lambda: 0.5)
    result = _turn(FakeAgent(), monkeypatch, command)
    assert result["completed"] is False and "deadline" in result["error"]


def test_transcript_write_failure_after_a_tool_ends_the_turn(tmp_path, monkeypatch):
    command = _worker(tmp_path, """
        send({"type": "tool-request", "turnId": tid, "callId": "c1", "name": "read_file", "args": {}})
        recv()
        send({"type": "event", "turnId": tid, "kind": "text", "text": "never shown as final"})
        send({"type": "done", "turnId": tid, "status": "finished"})
    """)

    def execute(agent, assistant_message, messages):
        FakeAgent._default_execute(agent, assistant_message, messages)
        agent._incremental_persistence_failed = True

    result = _turn(FakeAgent(execute=execute), monkeypatch, command)
    assert result["completed"] is False and "transcript" in result["error"]


def test_a_failed_final_flush_is_reported_not_hidden(tmp_path, monkeypatch):
    command = _worker(tmp_path, """
        send({"type": "event", "turnId": tid, "kind": "text", "text": "answer"})
        send({"type": "done", "turnId": tid, "status": "finished"})
    """)
    agent = FakeAgent()
    agent._flush_messages_to_session_db = lambda messages, history=None: False
    result = _turn(agent, monkeypatch, command)
    assert result["completed"] is False and "transcript" in result["error"]


def test_each_turn_gets_a_fresh_worker_state_and_the_current_model(tmp_path, monkeypatch):
    record = tmp_path / "record.jsonl"
    command = _worker(tmp_path, f"""
        import os
        with open({str(record)!r}, "a") as f:
            f.write(json.dumps({{"pid": os.getpid(), "home": os.environ["HOME"], "model": start["model"]}}) + "\\n")
        send({{"type": "done", "turnId": tid, "status": "finished"}})
    """)
    agent = FakeAgent()
    _turn(agent, monkeypatch, command)
    agent.model = "claude-sonnet-4-6"  # a /model switch between turns
    _turn(agent, monkeypatch, command)
    first, second = (json.loads(line) for line in record.read_text().splitlines())
    assert first["pid"] != second["pid"] and first["home"] != second["home"]
    assert [first["model"], second["model"]] == ["composer-2.5", "claude-sonnet-4-6"]
    assert not os.path.exists(first["home"]) and not os.path.exists(second["home"])


def test_resumed_history_is_context_only_and_never_redispatched(tmp_path, monkeypatch):
    command = _worker(tmp_path, """
        prompt = start["prompt"]
        assert "[TOOL RESULT]" in prompt and "earlier body" in prompt
        send({"type": "event", "turnId": tid, "kind": "text", "text": "no tools needed"})
        send({"type": "done", "turnId": tid, "status": "finished"})
    """)
    history = [
        {"role": "user", "content": "read it"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "old", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "old", "name": "read_file", "content": "earlier body"},
        {"role": "assistant", "content": "it says earlier body"},
        {"role": "user", "content": "thanks, now what?"},
    ]
    agent = FakeAgent()
    result = _turn(agent, monkeypatch, command, messages=history, user="thanks, now what?")
    assert result["completed"] is True
    assert agent.executed == []  # historical tool rows never dispatch again
