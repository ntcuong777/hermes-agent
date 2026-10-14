"""Cursor SDK runtime on gateway surfaces: interim commentary, per-session approvals, stop, isolation.

Gateway callbacks and the gateway approval queue are driven directly (no Telegram): they are the seams
the gateway turn runner registers, so what matters here is that the bridge uses them like the standard
loop does. Real AIAgent, fake worker process.
"""

from __future__ import annotations

import json
import sys
import textwrap
import threading
import time

import pytest

import agent.cursor_runtime as cr
import run_agent
from tools import approval as approval_mod
from tools import approval_context


def _worker(tmp_path, name, body):
    script = tmp_path / f"{name}.py"
    script.write_text(
        textwrap.dedent("""
        import json, sys
        def send(m): print(json.dumps(m), flush=True)
        def recv(): return json.loads(sys.stdin.readline())
        def event(kind, text): send({"type": "event", "turnId": tid, "kind": kind, "text": text})
        send({"type": "ready"})
        start = recv(); tid = start["turnId"]
    """)
        + textwrap.dedent(body)
    )
    return [sys.executable, str(script)]


def _agent():
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


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "test-key-not-real")
    monkeypatch.setattr(cr, "_CANCEL_GRACE_SECONDS", 0.5)


def _read_then_text(tmp_path, note):
    return _worker(
        tmp_path,
        "w",
        f"""
        event("text", "Let me look. ")
        send({{"type": "tool-request", "turnId": tid, "callId": "c1", "name": "read_file",
               "args": {{"path": {json.dumps(str(note))}}}}})
        recv()
        event("text", "Done.")
        send({{"type": "done", "turnId": tid, "status": "finished"}})
    """,
    )


def test_a_surface_without_streaming_still_gets_the_commentary_before_a_tool(
    tmp_path, monkeypatch
):
    note = tmp_path / "n.txt"
    note.write_text("x")
    monkeypatch.setattr(cr, "_worker_command", lambda: _read_then_text(tmp_path, note))
    agent, interim = _agent(), []
    agent.interim_assistant_callback = lambda text, already_streamed=False: (
        interim.append((text, already_streamed))
    )
    result = agent.run_conversation("go")
    assert result["completed"] is True
    assert interim == [("Let me look.", False)]  # exactly once


def test_a_streaming_surface_is_told_the_commentary_was_already_streamed(
    tmp_path, monkeypatch
):
    note = tmp_path / "n.txt"
    note.write_text("x")
    monkeypatch.setattr(cr, "_worker_command", lambda: _read_then_text(tmp_path, note))
    agent, interim, streamed = _agent(), [], []
    agent.interim_assistant_callback = lambda text, already_streamed=False: (
        interim.append((text, already_streamed))
    )
    agent.stream_delta_callback = lambda t: streamed.append(t) if t else None
    agent.run_conversation("go")
    assert [flag for _, flag in interim] == [True]
    assert "".join(streamed).count("Let me look.") == 1  # never shown twice


def _gateway_session(monkeypatch, key, asked):
    for name in ("HERMES_YOLO_MODE", "HERMES_CRON_SESSION", "HERMES_INTERACTIVE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    monkeypatch.setenv("HERMES_SESSION_KEY", key)
    monkeypatch.setattr(approval_mod, "_YOLO_MODE_FROZEN", False)
    monkeypatch.setattr(
        approval_context,
        "_get_approval_config",
        lambda: {"mode": "manual", "timeout": 30},
    )
    approval_mod._gateway_queues.clear()
    approval_mod._gateway_notify_cbs.clear()
    approval_mod._session_approved.clear()
    approval_mod.register_gateway_notify(key, lambda data: asked.append((key, data)))
    approval_mod.register_gateway_notify(
        "other-chat", lambda data: asked.append(("other-chat", data))
    )


def _rm_worker(tmp_path, victim):
    return _worker(
        tmp_path,
        "rm",
        f"""
        send({{"type": "tool-request", "turnId": tid, "callId": "c1", "name": "terminal",
               "args": {{"command": "rm -rf {victim}"}}}})
        reply = recv()
        event("text", "approved" if not reply["isError"] else "denied")
        send({{"type": "done", "turnId": tid, "status": "finished"}})
    """,
    )


def _run_in_thread(agent):
    box = {}
    thread = threading.Thread(
        target=lambda: box.update(result=agent.run_conversation("go"))
    )
    thread.start()
    return thread, box


def _wait_for(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not predicate():
        time.sleep(0.05)
    assert predicate(), "timed out"


def test_an_approval_prompt_goes_to_its_own_session_and_the_answer_decides(
    tmp_path, monkeypatch
):
    victim, asked = tmp_path / "victim", []
    victim.mkdir()
    (victim / "keep.txt").write_text("keep")
    _gateway_session(monkeypatch, "chat-A", asked)
    monkeypatch.setattr(cr, "_worker_command", lambda: _rm_worker(tmp_path, victim))
    thread, box = _run_in_thread(_agent())
    _wait_for(lambda: asked)
    assert [key for key, _ in asked] == ["chat-A"]  # never the other chat
    assert (victim / "keep.txt").exists()  # nothing ran while the prompt was pending
    approval_mod.resolve_gateway_approval("chat-A", "deny")
    thread.join(timeout=30)
    assert box["result"]["final_response"] == "denied"
    assert (victim / "keep.txt").read_text() == "keep"


def test_stopping_during_a_pending_approval_fails_closed_and_a_late_answer_changes_nothing(
    tmp_path, monkeypatch
):
    victim, asked = tmp_path / "victim", []
    victim.mkdir()
    (victim / "keep.txt").write_text("keep")
    _gateway_session(monkeypatch, "chat-S", asked)
    monkeypatch.setattr(cr, "_worker_command", lambda: _rm_worker(tmp_path, victim))
    agent = _agent()
    thread, box = _run_in_thread(agent)
    _wait_for(lambda: asked)
    agent.interrupt("stop")
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert box["result"]["interrupted"] is True and box["result"]["completed"] is False
    approval_mod.resolve_gateway_approval(
        "chat-S", "once"
    )  # the user answers after the stop
    time.sleep(0.3)
    assert (victim / "keep.txt").read_text() == "keep"


def test_successive_chats_keep_their_own_worker_state(tmp_path, monkeypatch):
    records = tmp_path / "records"
    records.mkdir()

    def command_for(name):
        return _worker(
            tmp_path,
            name,
            f"""
            import os
            event("text", {name!r})
            open({str(records)!r} + "/" + {name!r}, "w").write(os.environ["HOME"])
            send({{"type": "done", "turnId": tid, "status": "finished"}})
        """,
        )

    commands = {
        "chat-one": command_for("chat-one"),
        "chat-two": command_for("chat-two"),
    }
    outputs = {}

    def run(name):
        agent = _agent()
        monkeypatch.setattr(cr, "_worker_command", lambda: commands[name])
        outputs[name] = agent.run_conversation("go")["final_response"]

    # Sequential on purpose: the worker command is a process-wide patch, so true overlap would race the test.
    first = threading.Thread(target=run, args=("chat-one",))
    first.start()
    first.join()
    second = threading.Thread(target=run, args=("chat-two",))
    second.start()
    second.join()
    assert outputs == {"chat-one": "chat-one", "chat-two": "chat-two"}
    homes = {p.name: p.read_text() for p in records.iterdir()}
    assert homes["chat-one"] != homes["chat-two"]
