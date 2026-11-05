"""A worker that dies while Hermes runs its tool call must report why, not just that its pipe closed.

The worker writes its own terminal ``failed`` line (e.g. ``sdk-error`` when the SDK run broke) and exits;
Hermes only learns the pipe is gone when it answers the tool request. The turn error must carry the
worker's fixed-vocabulary reason instead of the bare "worker closed" text.
"""

import sys
import textwrap

import agent.cursor_runtime as cr
from tests.waiting import wait_until


def _dead_worker(tmp_path, body: str) -> cr._Worker:
    script = tmp_path / "dying_worker.py"
    script.write_text(
        "import json, sys\n"
        "def send(m): print(json.dumps(m), flush=True)\n" + textwrap.dedent(body)
    )
    worker = cr._Worker(
        [sys.executable, str(script)], {"PATH": ""}, str(tmp_path)
    )
    wait_until(lambda: worker._proc.poll() is not None, what="worker exited")
    wait_until(lambda: worker.inbox.qsize() >= 1, what="worker output read")
    return worker


def test_pipe_closed_after_a_worker_failure_reports_the_workers_reason(tmp_path):
    worker = _dead_worker(
        tmp_path,
        """
        send({"type": "failed", "turnId": "t1", "code": "sdk-error"})
        """,
    )
    with worker:
        turn = cr._Turn(object(), worker, [], "task")
        turn.turn_id = "t1"
        outcome = turn._reply("c1", "tool output", False)
    assert outcome is not None and outcome.status == "error"
    assert outcome.error == cr._FAILURE_TEXT["sdk-error"]


def test_pipe_closed_without_a_worker_reason_keeps_the_plain_text(tmp_path):
    worker = _dead_worker(
        tmp_path,
        """
        send({"type": "event", "turnId": "t1", "kind": "progress", "text": "x"})
        """,
    )
    with worker:
        turn = cr._Turn(object(), worker, [], "task")
        turn.turn_id = "t1"
        outcome = turn._reply("c1", "tool output", False)
    assert outcome is not None
    assert outcome.error == "worker closed before the tool result was delivered"
