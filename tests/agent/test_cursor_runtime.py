"""Cursor SDK runtime host: one user turn across worker protocol, executor and transcript.

A fake worker process speaks the real line protocol (hermes/cursor-runtime/worker-protocol.ts), so
these tests cover spawn, events, tool bridging, cancellation and failure without the SDK or network.
"""

import json
import sys
import textwrap
import threading
from types import SimpleNamespace

import agent.cursor_runtime as cr
import pytest

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "Run",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _worker(tmp_path, body: str):
    script = tmp_path / "fake_worker.py"
    script.write_text(
        textwrap.dedent("""
        import json, sys
        def send(m): print(json.dumps(m), flush=True)
        def recv(): return json.loads(sys.stdin.readline())
        send({"type": "ready"})
        start = recv()
        tid = start["turnId"]
    """)
        + textwrap.dedent(body)
    )
    return [sys.executable, str(script)]


class FakeAgent:
    """Just the surface the host touches; _execute_tool_calls stands in for the live executor."""

    def __init__(self, execute=None):
        self.tools = TOOLS
        self.valid_tool_names = {"read_file", "terminal"}
        self.model = "composer-2.5"
        self.session_id = "s1"
        self._session_db = None
        self._cached_system_prompt = "SYSTEM"
        self.ephemeral_system_prompt = None
        self._interrupt_requested = False
        self._interrupt_message = None
        self._incremental_persistence_failed = False
        self._iters_since_skill = 0
        self._skill_nudge_interval = 0
        self.deltas, self.reasoning, self.executed, self.flushes = [], [], [], []
        self._execute = execute or self._default_execute

    def _fire_stream_delta(self, text):
        self.deltas.append(text)

    def _fire_reasoning_delta(self, text, inline=False):
        self.reasoning.append(text)

    def _flush_messages_to_session_db(self, messages, history=None):
        self.flushes.append([m["role"] for m in messages])
        return True

    def _build_assistant_message(self, assistant_message, finish_reason):
        row = {
            "role": "assistant",
            "content": assistant_message.content or "",
            "finish_reason": finish_reason,
        }
        if assistant_message.tool_calls:
            row["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.function.name,
                        "arguments": c.function.arguments,
                    },
                }
                for c in assistant_message.tool_calls
            ]
        return row

    def _execute_tool_calls(
        self, assistant_message, messages, task_id, api_call_count=0
    ):
        self._execute(self, assistant_message, messages)

    @staticmethod
    def _default_execute(agent, assistant_message, messages):
        call = assistant_message.tool_calls[0]
        agent.executed.append((call.function.name, json.loads(call.function.arguments)))
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.function.name,
                "content": "FILE BODY",
            }
        )

    def clear_interrupt(self):
        self._interrupt_requested = False
        self._interrupt_message = None


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    # _run() assigns cr._worker_command directly; registering it here restores the original afterwards.
    monkeypatch.setattr(cr, "_worker_command", cr._worker_command)
    monkeypatch.setenv("CURSOR_API_KEY", "test-key-not-real")
    monkeypatch.setattr(
        cr,
        "stage_tool_call_message",
        lambda agent, assistant_message, finish_reason, messages: (
            agent._build_assistant_message(assistant_message, finish_reason),
            False,
        ),
    )


def _run(agent, command, messages=None, user="read it"):
    cr_messages = (
        messages if messages is not None else [{"role": "user", "content": user}]
    )
    cr_command = command

    def fake_command():
        return cr_command

    cr._worker_command = fake_command
    return cr.run_cursor_sdk_turn(
        agent,
        user_message=user,
        original_user_message=user,
        messages=cr_messages,
        effective_task_id="task",
    )


def test_read_tool_round_trips_through_the_executor_and_transcript(tmp_path):
    command = _worker(
        tmp_path,
        """
        send({"type": "event", "turnId": tid, "kind": "thinking", "text": "plan"})
        send({"type": "event", "turnId": tid, "kind": "text", "text": "Reading. "})
        send({"type": "tool-request", "turnId": tid, "callId": "c1", "name": "read_file", "args": {"path": "a.txt"}})
        result = recv()
        assert result["type"] == "tool-result" and result["callId"] == "c1"
        send({"type": "event", "turnId": tid, "kind": "text", "text": "It says " + result["text"]})
        send({"type": "done", "turnId": tid, "status": "finished"})
    """,
    )
    agent = FakeAgent()
    result = _run(agent, command)
    assert result["completed"] is True and result["error"] is None
    assert result["final_response"] == "It says FILE BODY"
    assert agent.executed == [("read_file", {"path": "a.txt"})]
    assert agent.reasoning == ["plan"]
    assert "".join(agent.deltas) == "Reading. It says FILE BODY"
    assert [m["role"] for m in result["messages"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert result["messages"][1]["content"] == "Reading. "
    assert result["messages"][1]["tool_calls"][0]["function"]["name"] == "read_file"
    assert result["agent_persisted"] is True
    # the tool-call row is durable before the tool runs
    assert agent.flushes[0] == ["user", "assistant"]


def test_every_enabled_tool_is_offered_and_unknown_names_refuse(tmp_path):
    command = _worker(tmp_path, """
        assert sorted(t["name"] for t in start["tools"]) == ["read_file", "terminal"]
        send({"type": "tool-request", "turnId": tid, "callId": "c1", "name": "ghost", "args": {}})
        refusal = recv()
        assert refusal["isError"] is True
        send({"type": "event", "turnId": tid, "kind": "text", "text": "refused"})
        send({"type": "done", "turnId": tid, "status": "finished"})
    """)
    agent = FakeAgent()
    result = _run(agent, command)
    assert result["completed"] is True
    assert agent.executed == []  # refused, never dispatched
    assert [m["role"] for m in result["messages"]] == ["user", "assistant"]


def test_worker_failure_ends_the_turn_without_fallback_or_secret_text(tmp_path):
    command = _worker(
        tmp_path,
        """
        send({"type": "failed", "turnId": tid, "code": "native-tool"})
    """,
    )
    result = _run(FakeAgent(), command)
    assert result["completed"] is False and result["partial"] is True
    assert "native tool" in result["error"]
    assert "test-key-not-real" not in json.dumps(result, default=str)


def test_worker_death_without_terminal_message_is_a_failed_turn(tmp_path):
    command = _worker(tmp_path, "sys.exit(3)")
    result = _run(FakeAgent(), command)
    assert result["completed"] is False
    assert "worker" in result["error"]


def test_missing_credential_fails_before_spawning(tmp_path, monkeypatch):
    monkeypatch.delenv("CURSOR_API_KEY")
    spawned = []
    monkeypatch.setattr(cr.subprocess, "Popen", lambda *a, **k: spawned.append(1))
    result = _run(FakeAgent(), [sys.executable, "-c", "pass"])
    assert result["completed"] is False and "CURSOR_API_KEY" in result["error"]
    assert spawned == []


def test_user_interrupt_cancels_the_worker_and_runs_no_more_tools(tmp_path):
    command = _worker(
        tmp_path,
        """
        send({"type": "event", "turnId": tid, "kind": "text", "text": "working"})
        cancel = recv()
        assert cancel == {"type": "cancel", "turnId": tid}
        send({"type": "done", "turnId": tid, "status": "cancelled"})
    """,
    )
    agent = FakeAgent()
    original = agent._fire_stream_delta

    def trip(text):
        original(text)
        agent._interrupt_requested = True
        agent._interrupt_message = "stop"

    agent._fire_stream_delta = trip
    result = _run(agent, command)
    assert result["interrupted"] is True and result["completed"] is False
    assert agent.executed == []
    assert (
        agent._interrupt_requested is False
    )  # consumed so it cannot poison the next turn


def test_persistence_failure_before_a_tool_runs_stops_the_turn(tmp_path):
    command = _worker(
        tmp_path,
        """
        send({"type": "tool-request", "turnId": tid, "callId": "c1", "name": "read_file", "args": {}})
        recv()
    """,
    )
    agent = FakeAgent()
    agent._flush_messages_to_session_db = lambda messages, history=None: False
    result = _run(agent, command)
    assert result["completed"] is False
    assert agent.executed == []


def test_prompt_carries_system_history_and_current_user(tmp_path):
    command = _worker(
        tmp_path,
        """
        p = start["prompt"]
        assert "SYSTEM" in p and "earlier question" in p and p.rstrip().endswith("read it")
        send({"type": "done", "turnId": tid, "status": "finished"})
    """,
    )
    history = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": "read it"},
    ]
    result = _run(FakeAgent(), command, messages=history)
    assert result["completed"] is True


def test_overlay_selects_cursor_sdk_for_the_cursor_provider_unless_pinned_to_chat():
    from hermes_cli.runtime_provider import _maybe_apply_cursor_sdk_runtime as pick

    assert (
        pick(
            provider="cursor",
            api_mode="chat_completions",
            model_cfg={"cursor_runtime": "sdk"},
            requested_provider="cursor",
        )
        == "cursor_sdk"
    )
    assert (
        pick(
            provider="cursor",
            api_mode="chat_completions",
            model_cfg={},
            requested_provider="cursor",
        )
        == "cursor_sdk"  # P41: choosing the provider is the switch
    )
    assert (
        pick(
            provider="cursor",
            api_mode="chat_completions",
            model_cfg={"cursor_runtime": "chat"},
            requested_provider="cursor",
        )
        == "chat_completions"
    )
    assert (
        pick(
            provider="openai",
            api_mode="codex_responses",
            model_cfg={"cursor_runtime": "sdk"},
            requested_provider="openai",
        )
        == "codex_responses"
    )
