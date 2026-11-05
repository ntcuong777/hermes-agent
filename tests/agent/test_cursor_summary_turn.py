"""Public one-shot summary turn on the Cursor worker (used by a context-engine plugin).

Cursor has no chat-completions route, so a compaction summary cannot go through the aux client; it
must ride the same worker the agent turn uses, with no tools. A fake worker speaks the real line protocol.
"""

import json
import sys
import textwrap

import agent.cursor_runtime as cr
import pytest
from agent.cursor_summary_turn import run_summary_turn


def _worker(tmp_path, body: str):
    script = tmp_path / "fake_worker.py"
    script.write_text(
        textwrap.dedent("""
        import json, sys
        def send(m): print(json.dumps(m), flush=True)
        send({"type": "ready"})
        start = json.loads(sys.stdin.readline())
        tid = start["turnId"]
    """)
        + textwrap.dedent(body)
    )
    return [sys.executable, str(script)]


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "test-key-not-real")


def _use(monkeypatch, command):
    monkeypatch.setattr(cr, "_worker_command", lambda: command)


def test_returns_the_streamed_text_and_offers_no_tools(tmp_path, monkeypatch):
    seen = tmp_path / "start.json"
    _use(
        monkeypatch,
        _worker(
            tmp_path,
            f"""
        open({str(seen)!r}, "w").write(json.dumps(start))
        send({{"type": "event", "turnId": tid, "kind": "text", "text": "## Goal\\n"}})
        send({{"type": "event", "turnId": tid, "kind": "thinking", "text": "hmm"}})
        send({{"type": "event", "turnId": tid, "kind": "text", "text": "done"}})
        send({{"type": "done", "turnId": tid, "status": "finished"}})
    """,
        ),
    )
    assert run_summary_turn("summarize this", model="grok-4.7") == "## Goal\ndone"
    start = json.loads(seen.read_text())
    assert start["tools"] == [] and start["model"] == "grok-4.7"
    assert start["prompt"] == "summarize this" and "effort" not in start


def test_a_worker_failure_raises_its_fixed_reason(tmp_path, monkeypatch):
    _use(
        monkeypatch,
        _worker(
            tmp_path,
            """
        send({"type": "failed", "turnId": tid, "code": "unknown-model"})
    """,
        ),
    )
    with pytest.raises(cr.CursorRuntimeError, match="not in your Cursor catalog"):
        run_summary_turn("x", model="nope")


def test_a_worker_that_dies_or_returns_nothing_raises(tmp_path, monkeypatch):
    _use(monkeypatch, _worker(tmp_path, "sys.exit(3)"))
    with pytest.raises(cr.CursorRuntimeError, match="worker"):
        run_summary_turn("x", model="m")
    _use(
        monkeypatch,
        _worker(
            tmp_path,
            """
        send({"type": "done", "turnId": tid, "status": "finished"})
    """,
        ),
    )
    with pytest.raises(cr.CursorRuntimeError, match="no text"):
        run_summary_turn("x", model="m")


def test_a_native_tool_attempt_or_non_finished_status_raises(tmp_path, monkeypatch):
    _use(
        monkeypatch,
        _worker(
            tmp_path,
            """
        send({"type": "done", "turnId": tid, "status": "cancelled"})
    """,
        ),
    )
    with pytest.raises(cr.CursorRuntimeError, match="cancelled"):
        run_summary_turn("x", model="m")


def test_a_stalled_worker_is_cut_at_the_deadline(tmp_path, monkeypatch):
    _use(
        monkeypatch,
        _worker(
            tmp_path,
            """
        import time
        time.sleep(30)
    """,
        ),
    )
    monkeypatch.setattr(cr, "_CANCEL_GRACE_SECONDS", 0.2)
    with pytest.raises(cr.CursorRuntimeError, match="deadline"):
        run_summary_turn("x", model="m", timeout=2.0)
