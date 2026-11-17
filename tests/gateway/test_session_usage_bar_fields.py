"""The shared gateway's ``session.usage`` frame carries plugin status-bar segments (hook ``transform_status_bar_fields``).

Bug (fork patch P40, now a plugin's job): ``display.status_bar.fields: [cost, quota]`` only reached the embedded
ticker (``tui_gateway/server.py::_get_usage``). A TUI attached to the shared gateway reads
``gateway/session_usage.py``, which never added them. The segments are plugin data now; this module pins that the
gateway path carries them too. Dispatch, validation and the embedded path: ``tests/tui_gateway/test_status_bar_fields.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway import session_usage
from hermes_cli import plugins


def _agent(**over):
    base = dict(
        model="claude-sonnet-5-5", provider="anthropic", base_url=None, session_api_calls=3, session_id="s1",
        context_compressor=None,
    )
    return SimpleNamespace(**{**base, **over})


@pytest.fixture
def bar(monkeypatch):
    mgr = plugins.PluginManager()
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: mgr)
    mgr._hooks["transform_status_bar_fields"] = [
        lambda fields, wanted, **_: [{"id": n, "text": f"{n}!"} for n in wanted if n in ("cost", "quota")]
    ]

    def set_fields(*fields):
        cfg = {"display": {"status_bar": {"fields": list(fields)}}}
        monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: cfg)

    return set_fields


def test_a_bar_that_lists_a_segment_gets_it_in_the_usage_frame_and_the_turn_end(bar):
    bar("model", "cost")
    stats = session_usage.usage_stats(_agent())
    rows = [{"id": "cost", "text": "cost!"}]
    assert session_usage.usage_frame(stats)["usage"]["status_fields"] == rows
    assert session_usage.tui_usage(stats)["status_fields"] == rows


def test_the_default_bar_gets_none(bar):
    bar()
    frame = session_usage.usage_frame(session_usage.usage_stats(_agent()))["usage"]
    assert "status_fields" not in frame


def test_a_failing_config_read_never_breaks_usage(monkeypatch):
    def boom():
        raise OSError("config unreadable")

    monkeypatch.setattr("hermes_cli.config.load_config_readonly", boom)
    stats = session_usage.usage_stats(_agent())
    assert stats["api_calls"] == 3 and "status_fields" not in session_usage.tui_usage(stats)
