"""Reporting: logs, status.json, events.jsonl, home-channel notices; all best effort."""

from __future__ import annotations

import json
import logging
import os
import threading

import pytest

from hermes_cli import core_patches as cp
from hermes_cli import core_patches_policy as policy
from hermes_cli import core_patches_report as report
from hermes_cli.core_patches_ast import write_lock_entry
from tests.core_patches.conftest import GREET_SHA, PROVENANCE

OWNER = "home-a"


def _up(original):
    def greet(name):
        return original(name).upper()
    return greet


def _install(target, *, sha=GREET_SHA, plugin_dir=None, name="up", critical=False):
    spec = cp.make_spec(plugin_id="demo", name=name, target=f"{target.name}:greet", kind="wrap", fn=_up,
                        expect_ast_sha=sha, plugin_dir=plugin_dir, critical=critical)
    key = cp.stage(spec, OWNER, granted=True, declared=True)
    cp.activate(key, OWNER)
    return key


def _events():
    path = policy.state_dir() / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


def _status():
    return json.loads((policy.state_dir() / "status.json").read_text(encoding="utf-8"))


@pytest.fixture
def surfaces_gateway(monkeypatch):
    """A gateway process: notices are queued; capture what would be sent instead of starting a sender."""
    sent = []
    monkeypatch.setattr(report, "_send_when_possible", lambda text: sent.append(text))
    monkeypatch.setattr(report.threading, "Thread", _InlineThread)
    return sent


class _InlineThread:
    def __init__(self, target=None, args=(), daemon=None, name=None, **_):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


def test_apply_logs_one_summary_and_one_warning_per_skipped_patch(target, no_discovery, caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="hermes_cli.core_patches")
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    write_lock_entry(plugin, "old", f"{target.name}:greet", target.path.parent, provenance=PROVENANCE)
    _install(target, sha="2" * 64, plugin_dir=str(plugin), name="old")
    cp.apply_core_patches("test")
    cp.apply_core_patches("test")
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
                and "demo:old skipped-drift" in r.getMessage()]
    assert len(warnings) == 1
    (line,) = warnings
    assert "plugin=demo" in line and f"target={target.name}:greet" in line
    assert "recorded-upstream=e96221fa73d" in line and "(0.23.0)" in line
    assert "expected-sha=222222222222" in line and "current-sha=8ac9a553d27b" in line
    assert "--agent-brief old" in line
    summaries = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and "core patches (test)" in r.getMessage()]
    assert summaries and "1 patch(es): skipped-drift=1" in summaries[0]


def test_status_json_describes_the_running_code_and_each_patch(target, no_discovery, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_FORK_HEAD", "f" * 40)
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    write_lock_entry(plugin, "up", f"{target.name}:greet", target.path.parent, provenance=PROVENANCE)
    _install(target, plugin_dir=str(plugin))
    cp.apply_core_patches("gateway")
    status = _status()
    assert status["pid"] == os.getpid() and status["surface"] == "gateway" and status["fork_head"] == "f" * 40
    assert status["counts"] == {"active": 1} and set(status["upstream_ref"]) == {"commit", "release", "date"}
    row = status["patches"]["demo:up"]
    assert row["status"] == "active" and row["reason"] == PROVENANCE["reason"]
    assert row["expect_ast_sha"] == GREET_SHA == row["current_sha"] and row["upstream_commit"].startswith("e96221fa")
    assert row["last_changed"].endswith("Z") and status["started_at"].endswith("Z")


def test_events_record_drift_first_seen_once_and_recovery_with_before_and_after_hashes(target, no_discovery):
    _install(target, sha="3" * 64)
    cp.apply_core_patches("test")
    cp.apply_core_patches("test")  # same state: no second event
    assert [(e["event"], e["key"]) for e in _events()] == [("drift-first-seen", "demo:up")]
    cp._reset_for_tests()  # the next process: the patch was fixed, status.json still says drift
    _install(target)
    cp.apply_core_patches("test")
    recovered = _events()[-1]
    assert recovered["event"] == "drift-recovered" and recovered["recorded_sha"] == GREET_SHA
    assert recovered["before_sha"] == GREET_SHA and recovered["after_sha"] == GREET_SHA
    first = _events()[0]
    assert first["recorded_sha"] == "3" * 64 and first["after_sha"] == GREET_SHA


def test_the_event_file_rotates_past_the_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "load_settings", lambda: policy.Settings(events_max_bytes=4096))
    cp.refresh_settings()
    for index in range(80):
        report.event("error", key="demo:x", detail="x" * 100, n=index)
    directory = policy.state_dir()
    assert (directory / "events.jsonl.1").is_file()
    assert (directory / "events.jsonl").stat().st_size <= 4096 and (directory / "events.jsonl.1").stat().st_size <= 4096 + 300


def test_a_new_problem_is_announced_once_to_the_home_channel(target, no_discovery, surfaces_gateway):
    _install(target, sha="4" * 64)
    cp.apply_core_patches("gateway")
    cp.apply_core_patches("gateway")
    assert len(surfaces_gateway) == 1 and "demo:up" in surfaces_gateway[0]
    assert "skipped-drift" in surfaces_gateway[0] and "--agent-brief" not in surfaces_gateway[0]
    assert (policy.state_dir() / "notified.json").is_file()


def test_no_notice_with_notify_off_or_outside_the_gateway(target, no_discovery, surfaces_gateway, monkeypatch):
    _install(target, sha="4" * 64)
    cp.apply_core_patches("cli")  # not a gateway, no runner bound
    assert surfaces_gateway == []
    monkeypatch.setattr(policy, "load_settings", lambda: policy.Settings(notify=False))
    report._STATE["digest"] = ""
    cp.apply_core_patches("gateway")
    assert surfaces_gateway == []


def test_a_notice_that_cannot_be_claimed_never_raises(target, no_discovery, surfaces_gateway, monkeypatch):
    def broken(directory, fingerprints):
        raise OSError("disk full")
    monkeypatch.setattr(report, "_claim_many", broken)
    _install(target, sha="4" * 64)
    assert [r.status for r in cp.apply_core_patches("gateway")] == [cp.SKIPPED_DRIFT]
    assert surfaces_gateway == []


def test_an_unwritable_state_directory_never_breaks_apply(target, no_discovery, tmp_path, monkeypatch):
    blocker = tmp_path / "home"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(policy, "state_dir", lambda home=None: blocker / "core-patches")
    _install(target)
    assert [r.status for r in cp.apply_core_patches("test")] == [cp.ACTIVE]


def test_identity_reads_the_stamp_files_without_git(tmp_path, monkeypatch):
    (tmp_path / "hermes-upstream-commit").write_text("a" * 40 + "\n", encoding="utf-8")
    (tmp_path / "hermes-upstream-release").write_text("0.23.0\n", encoding="utf-8")
    monkeypatch.delenv("HERMES_FORK_HEAD", raising=False)
    assert report.identity(tmp_path)["upstream_ref"] == {"commit": "a" * 40, "release": "0.23.0", "date": "unknown"}


def test_status_survives_a_corrupt_previous_file(target, no_discovery):
    directory = policy.state_dir()
    directory.mkdir(parents=True)
    (directory / "status.json").write_text("{corrupt", encoding="utf-8")
    _install(target)
    cp.apply_core_patches("test")
    assert _status()["counts"] == {"active": 1}


def test_identity_prefers_the_environment_a_nix_install_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_UPSTREAM_COMMIT", "b" * 40)
    monkeypatch.setenv("HERMES_UPSTREAM_RELEASE", "0.24.0")
    assert report._upstream_ref(tmp_path) == {"commit": "b" * 40, "release": "0.24.0", "date": "unknown"}


def test_a_start_without_patches_writes_nothing(no_discovery):
    assert cp.apply_core_patches("cli") == ()
    assert not (policy.state_dir() / "status.json").exists() and report._STATE["identity"] is None


def test_an_event_written_under_the_io_lock_by_the_same_thread_does_not_deadlock(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "safe_state_dir", lambda: tmp_path)
    done = threading.Event()

    def under_the_lock():
        report.event("applied", key="demo:x")  # _write_files holds _IO when it reaches event()

    def caller():
        with report._IO:
            under_the_lock()
        done.set()
    worker = threading.Thread(target=caller, daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive() and done.is_set()


def test_the_notice_names_the_key_status_and_exception_type_and_nothing_of_the_detail(tmp_path, monkeypatch):
    sent, delivered = [], threading.Event()

    def sender(text):
        sent.append(text)
        delivered.set()
    monkeypatch.setattr(report, "_send_when_possible", sender)
    monkeypatch.setattr(policy, "safe_state_dir", lambda: tmp_path)
    row = {"plugin": "demo", "name": "up", "target": "pkg.mod:greet", "status": "skipped-error",
           "expect_ast_sha": "a" * 64, "current_sha": "b" * 64, "upstream_commit": "c" * 40,
           "detail": "/Users/x/secret/path: boom", "last_error": "RuntimeError: /Users/x/secret/path: boom"}
    report.notify([row], [], policy.Settings(), "gateway")
    assert delivered.wait(5) and len(sent) == 1
    text = sent[0]
    assert "/Users/x" not in text and "secret" not in text and "boom" not in text
    assert "demo:up" in text and "skipped-error" in text and "RuntimeError" in text
    assert "a" * 12 not in text and "pkg.mod" not in text  # no fingerprints, no targets: key, status, type only


def test_the_critical_notice_names_key_and_status_only_while_the_error_log_keeps_the_detail(
        surfaces_gateway, monkeypatch, caplog, tmp_path):
    from types import SimpleNamespace

    caplog.set_level(logging.ERROR, logger="hermes_cli.core_patches")
    monkeypatch.setattr(policy, "safe_state_dir", lambda: tmp_path)
    monkeypatch.setattr(policy, "load_settings", lambda: policy.Settings())
    monkeypatch.setitem(report._STATE, "surface", "gateway")
    record = SimpleNamespace(spec=SimpleNamespace(key="demo:up", target="pkg.mod:greet", plugin_dir=None),
                             status=cp.SKIPPED_ERROR, detail="/Users/x/secret/path: boom", critical=True)
    report.critical_alert(cp.critical_problems([record]), cp.critical_notice_lines([record]))
    assert len(surfaces_gateway) == 1
    notice = surfaces_gateway[0]
    assert "demo:up" in notice and "skipped-error" in notice
    assert "secret/path" not in notice and "boom" not in notice
    logged = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("/Users/x/secret/path: boom" in message for message in logged)
