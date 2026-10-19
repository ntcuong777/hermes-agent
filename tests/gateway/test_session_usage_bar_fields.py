"""The shared gateway's ``session.usage`` frame carries the status bar's opt-in cost and quota (fork patch P40).

Bug: ``display.status_bar.fields: [cost, quota]`` only reached the embedded ticker
(``tui_gateway/server.py::_get_usage``). A TUI attached to the shared gateway reads ``gateway/session_usage.py``,
which never added them, so the bar showed no cost and no quota whatever the config said.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway import session_usage


def _agent(**over):
    base = dict(
        model="claude-sonnet-5-5", provider="anthropic", base_url=None, session_api_calls=3,
        session_estimated_cost_usd=0.4231, session_cost_status="estimated", context_compressor=None,
    )
    return SimpleNamespace(**{**base, **over})


@pytest.fixture
def bar(monkeypatch):
    def set_fields(*fields):
        cfg = {"display": {"status_bar": {"fields": list(fields)}}}
        monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: cfg)

    return set_fields


def test_a_bar_that_lists_cost_gets_it_in_the_usage_frame_and_the_turn_end(bar):
    bar("model", "cost")
    stats = session_usage.usage_stats(_agent())
    frame = session_usage.usage_frame(stats)["usage"]
    assert frame["cost_usd"] == 0.4231 and frame["cost_status"] == "estimated"
    complete = session_usage.tui_usage(stats)
    assert complete["cost_usd"] == 0.4231


def test_the_default_bar_gets_neither_and_polls_no_quota(bar, monkeypatch):
    bar()
    asked: list = []
    monkeypatch.setattr("agent.account_usage_cache.refresh_account_usage_async", lambda p: asked.append(p) or [])
    frame = session_usage.usage_frame(session_usage.usage_stats(_agent()))["usage"]
    assert "cost_usd" not in frame and "cost_status" not in frame and "quota" not in frame
    assert asked == []


def test_calls_with_no_price_say_unpriced_so_the_bar_shows_n_a(bar):
    bar("cost")
    stats = session_usage.usage_stats(_agent(session_estimated_cost_usd=0, session_cost_status="unknown"))
    assert session_usage.tui_usage(stats)["cost_status"] == "unpriced"
    assert "cost_usd" not in session_usage.tui_usage(stats)


def test_a_failing_config_read_never_breaks_usage(monkeypatch):
    def boom():
        raise OSError("config unreadable")

    monkeypatch.setattr("hermes_cli.config.load_config_readonly", boom)
    stats = session_usage.usage_stats(_agent())
    assert stats["api_calls"] == 3 and "cost_usd" not in session_usage.tui_usage(stats)
