"""Tracer bullet: a real AIAgent in cursor_sdk mode runs read_file through the live executor.

A fake worker process stands in for the Cursor SDK; everything on the Hermes side is real: agent init
with the new api_mode, run_conversation's runtime branch, the sequential executor and its transcript
commit. Proves R1 (prompt completes on the Cursor runtime) and the read slice of R2.
"""

from __future__ import annotations

import json
import sys
import textwrap

import pytest

import agent.cursor_runtime as cr
import run_agent


def _fake_worker(tmp_path, target):
    script = tmp_path / "fake_worker.py"
    script.write_text(
        textwrap.dedent(f"""
        import json, sys
        def send(m): print(json.dumps(m), flush=True)
        def recv(): return json.loads(sys.stdin.readline())
        send({{"type": "ready"}})
        start = recv()
        tid = start["turnId"]
        assert [t["name"] for t in start["tools"]] == ["read_file"], start["tools"]
        send({{"type": "event", "turnId": tid, "kind": "thinking", "text": "need the file"}})
        send({{"type": "tool-request", "turnId": tid, "callId": "c1", "name": "read_file",
               "args": {{"path": {json.dumps(str(target))}}}}})
        result = recv()
        assert "TRACER-CONTENT" in result["text"], result
        send({{"type": "event", "turnId": tid, "kind": "text", "text": "The file says TRACER-CONTENT."}})
        send({{"type": "done", "turnId": tid, "status": "finished"}})
    """)
    )
    return [sys.executable, str(script)]


@pytest.fixture
def agent():
    return run_agent.AIAgent(
        api_key="stub",
        base_url="https://stub.invalid",
        provider="cursor",
        model="composer-2.5",
        api_mode="cursor_sdk",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
        enabled_toolsets=["file", "terminal"],
    )


def test_run_conversation_reads_a_file_through_the_live_executor(
    tmp_path, monkeypatch, agent
):
    target = tmp_path / "note.txt"
    target.write_text("TRACER-CONTENT\n")
    monkeypatch.setenv("CURSOR_API_KEY", "test-key-not-real")
    monkeypatch.setattr(cr, "_worker_command", lambda: _fake_worker(tmp_path, target))
    streamed, reasoning = [], []
    agent.stream_delta_callback = lambda text: streamed.append(text)
    agent.reasoning_callback = lambda text: reasoning.append(text)

    result = agent.run_conversation("what is in note.txt?")

    assert result["completed"] is True, result.get("error")
    assert result["final_response"] == "The file says TRACER-CONTENT."
    roles = [m["role"] for m in result["messages"]]
    assert roles[-3:] == ["assistant", "tool", "assistant"]
    tool_row = result["messages"][-2]
    assert "TRACER-CONTENT" in tool_row["content"]
    assert tool_row["tool_call_id"] == result["messages"][-3]["tool_calls"][0]["id"]
    assert reasoning == ["need the file"]
    assert "TRACER-CONTENT." in "".join(t for t in streamed if t)


def test_unadvertised_tool_never_reaches_the_executor(tmp_path, monkeypatch, agent):
    script = tmp_path / "w.py"
    script.write_text(
        textwrap.dedent("""
        import json, sys
        def send(m): print(json.dumps(m), flush=True)
        def recv(): return json.loads(sys.stdin.readline())
        send({"type": "ready"})
        start = recv(); tid = start["turnId"]
        send({"type": "tool-request", "turnId": tid, "callId": "c1", "name": "terminal",
              "args": {"command": "touch should-not-exist"}})
        assert recv()["isError"] is True
        send({"type": "done", "turnId": tid, "status": "finished"})
    """)
    )
    monkeypatch.setenv("CURSOR_API_KEY", "test-key-not-real")
    monkeypatch.setattr(cr, "_worker_command", lambda: [sys.executable, str(script)])
    monkeypatch.chdir(tmp_path)

    result = agent.run_conversation("run a command")

    assert result["completed"] is True
    assert not (tmp_path / "should-not-exist").exists()
    assert all(m["role"] != "tool" for m in result["messages"])
