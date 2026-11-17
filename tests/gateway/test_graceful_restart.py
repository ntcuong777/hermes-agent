"""gateway/graceful_restart.py: the marker file, the heartbeat, the restart request seam and `--when-idle`."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from argparse import Namespace
from pathlib import Path

import pytest

from gateway import busy_report, graceful_restart as gr


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(busy_report, "_runner_ref", None)
    monkeypatch.setattr(busy_report, "_loop", None)
    return tmp_path


@pytest.mark.parametrize("text,seconds", [("45s", 45), ("90m", 5400), ("6h", 21600), ("2d", 172800), ("30", 30),
                                           (" 1.5h ", 5400), ("0", 0.0), ("none", 0.0), ("", None), (None, None)])
def test_parse_duration(text, seconds):
    assert gr.parse_duration(text) == seconds


def test_parse_duration_rejects_garbage():
    with pytest.raises(ValueError):
        gr.parse_duration("soon")


def test_the_marker_round_trips_with_the_documented_fields(_home):
    written = gr.write_marker(reason="rebuild", mode="wait", max_wait=3600, requested_by="hgr", now=1000.0)
    assert json.loads((_home / "restart-requested.json").read_text()) == written
    assert set(written) == {"requested_at", "reason", "mode", "max_wait", "requested_by"}
    assert gr.read_marker() == written
    assert gr.clear_marker() is True and gr.clear_marker() is False and gr.read_marker() is None


def test_a_zero_wait_stays_no_limit_and_a_missing_mode_is_wait(_home):
    (_home / "restart-requested.json").write_text(json.dumps({"requested_at": 5, "max_wait": 0}))
    marker = gr.read_marker()
    assert marker["mode"] == "wait" and marker["max_wait"] == 0.0 and marker["reason"] == ""


@pytest.mark.parametrize("mode", ["drain", "weird", 3])
def test_a_marker_with_an_unknown_mode_is_rejected(_home, caplog, mode):
    (_home / "restart-requested.json").write_text(json.dumps({"requested_at": 5, "mode": mode}))
    with caplog.at_level("WARNING", logger="gateway.run"):
        assert gr.read_marker() is None
    assert "unknown mode" in caplog.text


def test_a_far_future_requested_at_is_clamped_to_now_and_flagged(_home):
    (_home / "restart-requested.json").write_text(json.dumps({"requested_at": time.time() + 10 * 365 * 86400}))
    marker = gr.read_marker()
    assert marker["requested_at"] <= time.time() and marker["clamped"] is True
    (_home / "restart-requested.json").write_text(json.dumps({"requested_at": 5}))
    assert "clamped" not in gr.read_marker() and gr.read_marker()["requested_at"] == 5


@pytest.mark.parametrize("body", ["not json", "[]", '{"reason": "x"}', '{"requested_at": "yesterday"}'])
def test_an_unusable_marker_reads_as_none(_home, body):
    (_home / "restart-requested.json").write_text(body)
    assert gr.read_marker() is None


@pytest.mark.parametrize("mode", ["now", "drain"])
def test_write_marker_rejects_an_unknown_mode(mode):
    with pytest.raises(ValueError):
        gr.write_marker(mode=mode)


def _status(_home, **fields):
    (_home / "graceful-restart").mkdir(exist_ok=True)
    (_home / "graceful-restart" / "status.json").write_text(json.dumps(fields))


def test_consumer_alive_needs_a_fresh_heartbeat_of_a_live_pid(_home):
    assert gr.consumer_alive() is False
    _status(_home, heartbeat_at=time.time(), pid=os.getpid())
    assert gr.consumer_alive() is True
    _status(_home, heartbeat_at=time.time() - 3600, pid=os.getpid())
    assert gr.consumer_alive() is False
    _status(_home, heartbeat_at=time.time(), pid=2_000_000_000)
    assert gr.consumer_alive() is False


class _Runner:
    def __init__(self, started=True):
        self.calls, self._restart_requested, self.started = [], False, started
        self._restart_task_started = False

    def request_restart(self, *, detached, via_service):
        self.calls.append((detached, via_service, threading.current_thread().name))
        self._restart_requested = True
        return self.started

    def _describe_active_work(self):
        return [{"kind": "delegation", "delegation_id": "d1", "goal": "g", "elapsed_s": 3}]


def _on_a_loop(runner):
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="gw-loop", daemon=True)
    thread.start()
    loop.call_soon_threadsafe(busy_report.bind_runner, runner)
    deadline = time.time() + 2
    while busy_report.current_runner() is None and time.time() < deadline:
        time.sleep(0.01)
    return loop, thread


def _stop(loop, thread):
    loop.call_soon_threadsafe(loop.stop)
    thread.join(2)


def test_request_graceful_restart_without_a_gateway_is_false():
    assert gr.request_graceful_restart("test") is False


@pytest.mark.parametrize("supervised,expect", [(True, (False, True)), (False, (True, False))])
def test_request_graceful_restart_takes_the_slash_restart_path_on_the_loop_thread(monkeypatch, caplog, supervised, expect):
    import gateway.restart as restart

    monkeypatch.setattr(restart, "is_gateway_supervisor_process", lambda *a, **k: supervised)
    monkeypatch.setattr(restart, "is_container_restart_context", lambda: False)
    runner = _Runner()
    loop, thread = _on_a_loop(runner)
    try:
        with caplog.at_level("WARNING", logger="gateway.run"):
            assert gr.request_graceful_restart("rebuild") is True
        assert runner.calls == [(*expect, "gw-loop")]
        assert "Graceful restart requested (rebuild)" in caplog.text and "d1@3s" in caplog.text
    finally:
        _stop(loop, thread)


def test_an_already_running_restart_counts_as_in_progress(monkeypatch):
    runner = _Runner(started=False)
    runner._restart_requested = True
    loop, thread = _on_a_loop(runner)
    try:
        assert gr.request_graceful_restart("again") is True
    finally:
        _stop(loop, thread)


def test_cmd_restart_when_idle_writes_the_marker_and_names_what_blocks(_home, capsys):
    _status(_home, heartbeat_at=time.time(), pid=os.getpid())
    (_home / "graceful-restart" / "busy.txt").write_text("busy: 1 blocking item(s)\nprocesses: 1 blocking of 1\n")
    gr.cmd_restart_when_idle(Namespace(max_wait="6h", reason="deploy"))
    out = capsys.readouterr().out
    assert "21600s" in out and "processes: 1 blocking of 1" in out
    marker = gr.read_marker()
    assert (marker["mode"], marker["max_wait"], marker["reason"], marker["requested_by"]) == ("wait", 21600.0, "deploy", "cli")


def test_cmd_restart_when_idle_exits_2_and_writes_nothing_when_no_consumer_is_alive(_home, capsys):
    with pytest.raises(SystemExit) as exit_info:
        gr.cmd_restart_when_idle(Namespace(max_wait=None, reason=None))
    assert exit_info.value.code == 2
    assert "no live graceful-restart plugin heartbeat" in capsys.readouterr().err
    assert gr.read_marker() is None


def test_cmd_restart_when_idle_names_a_bad_max_wait_instead_of_a_traceback(_home, capsys):
    _status(_home, heartbeat_at=time.time(), pid=os.getpid())
    with pytest.raises(SystemExit) as exit_info:
        gr.cmd_restart_when_idle(Namespace(max_wait="soon", reason=None))
    assert exit_info.value.code == 1
    assert "--max-wait" in capsys.readouterr().err and gr.read_marker() is None


def test_the_gateway_restart_parser_accepts_the_when_idle_flags():
    import argparse

    from hermes_cli.subcommands.gateway import build_gateway_parser

    root = argparse.ArgumentParser()
    build_gateway_parser(root.add_subparsers(dest="command"), cmd_gateway=lambda a: None, cmd_proxy=lambda a: None,
                         cmd_gateway_enroll=lambda a: None)
    args = root.parse_args(["gateway", "restart", "--when-idle", "--max-wait", "6h", "--reason", "x"])
    assert (args.when_idle, args.max_wait, args.reason) == (True, "6h", "x")
    plain = root.parse_args(["gateway", "restart"])
    assert (plain.when_idle, plain.max_wait) == (False, None)
    with pytest.raises(SystemExit):  # drain mode does not exist
        root.parse_args(["gateway", "restart", "--when-idle", "--drain"])


def test_gateway_restart_with_when_idle_only_writes_the_marker(_home, monkeypatch, capsys):
    from hermes_cli import gateway as gw

    _status(_home, heartbeat_at=time.time(), pid=os.getpid())
    monkeypatch.setattr(gw, "_refuse_from_inside_gateway", lambda *a, **k: None)
    monkeypatch.setattr(gw, "profile_lifecycle", lambda *a, **k: pytest.fail("must not reach the restart"), raising=False)
    assert gw._cmd_restart(Namespace(when_idle=True, max_wait="1h", reason="r")) is None
    assert gr.read_marker()["max_wait"] == 3600.0
