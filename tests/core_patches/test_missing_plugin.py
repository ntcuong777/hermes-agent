"""The startup check for a removed lane feature whose serving plugin is not loaded (hermes_cli/core_patches_missing.py).

The plugins' own side (their sources serve each id) is pinned in the lane-mirror plugin suite."""

from __future__ import annotations

import dataclasses
import logging
from types import SimpleNamespace

import pytest

from gateway import session_lane_features as features
from hermes_cli import core_patches_missing as missing
from hermes_cli import core_patches_policy as policy


def _loaded(name, *, enabled=True, error=None):
    return SimpleNamespace(manifest=SimpleNamespace(name=name), enabled=enabled, error=error)


def _manager(*plugins):
    return SimpleNamespace(_plugins={name: _loaded(name) for name in plugins})


ALL_THREE = ("lane-mirror", "chat-workspaces", "config-seams")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(missing, "_ANNOUNCED", [None])
    notices = []
    from hermes_cli import core_patches_report as report
    monkeypatch.setattr(report, "notify_missing_plugins", lambda lines: notices.append(list(lines)))
    return SimpleNamespace(notices=notices)


def test_the_plugin_absent_logs_one_error_and_sends_one_notice(caplog, _fresh):
    caplog.set_level(logging.ERROR, logger="hermes_cli.core_patches")
    first = missing.report_missing_plugins([], [])
    again = missing.report_missing_plugins([], [])
    assert set(first) == {"lane-mirror", "chat-workspaces", "config-seams"} and again == first
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and "lane features are not served" in r.getMessage()]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert "lane-mirror" in message and "pills" in message and "p27" in message and "workspaces" in message
    assert len(_fresh.notices) == 1
    assert any(line.startswith("lane-mirror is not loaded") for line in _fresh.notices[0])


def test_the_plugins_loaded_is_silent(caplog, _fresh):
    caplog.set_level(logging.ERROR, logger="hermes_cli.core_patches")
    gap = missing.report_missing_plugins([], [_manager(*ALL_THREE)])
    assert gap == {}
    assert not [r for r in caplog.records if "lane features are not served" in r.getMessage()]
    assert _fresh.notices == []


def test_an_old_core_with_nothing_removed_is_silent(monkeypatch, _fresh):
    monkeypatch.setattr(features, "REMOVED_FROM_CORE", frozenset())
    assert missing.report_missing_plugins([], []) == {}
    assert _fresh.notices == []


def test_a_notifier_that_fails_never_raises_and_the_log_line_stays(monkeypatch, caplog):
    from hermes_cli import core_patches_report as report

    def boom(lines):
        raise RuntimeError("notifier broke")
    monkeypatch.setattr(report, "notify_missing_plugins", boom)
    caplog.set_level(logging.ERROR, logger="hermes_cli.core_patches")
    assert set(missing.report_missing_plugins([], [])) == {"lane-mirror", "chat-workspaces", "config-seams"}
    assert [r for r in caplog.records if "lane features are not served" in r.getMessage()]


def test_the_switch_silences_it(monkeypatch, _fresh):
    base = policy.load_settings
    monkeypatch.setattr(policy, "load_settings", lambda: dataclasses.replace(base(), warn_missing_plugin=False))
    assert missing.report_missing_plugins([], []) == {}
    assert _fresh.notices == []


def test_discovery_still_running_is_silent(_fresh):
    assert missing.report_missing_plugins([], [], timed_out=True) == {}
    assert _fresh.notices == []


def test_a_disabled_or_errored_plugin_does_not_serve(_fresh):
    disabled = SimpleNamespace(_plugins={"x": _loaded("lane-mirror", enabled=False)})
    errored = SimpleNamespace(_plugins={"y": _loaded("lane-mirror", error="boom")})
    gap = missing.missing_by_plugin([], [disabled, errored], features.REMOVED_FROM_CORE, features.SERVED_BY)
    assert "lane-mirror" in gap and "gates" in gap["lane-mirror"]


def test_an_ungranted_plugin_counts_as_missing(_fresh):
    record = SimpleNamespace(spec=SimpleNamespace(plugin_id="lane-mirror"), status="skipped-no-grant")
    gap = missing.missing_by_plugin([record], [_manager("lane-mirror")], features.REMOVED_FROM_CORE, features.SERVED_BY)
    assert "lane-mirror" in gap
    assert "chat-workspaces" in gap and "config-seams" in gap  # not loaded at all


def test_the_switch_parses_with_default_on():
    assert policy.parse_settings({}).warn_missing_plugin is True
    assert policy.parse_settings({"warn_missing_plugin": False}).warn_missing_plugin is False
    assert policy.parse_settings({"warn_missing_plugin": "no"}).warn_missing_plugin is True


def test_every_removed_feature_names_the_plugin_that_serves_it():
    assert set(features.REMOVED_FROM_CORE) == set(features.SERVED_BY)
    assert set(features.SERVED_BY.values()) == set(ALL_THREE)
