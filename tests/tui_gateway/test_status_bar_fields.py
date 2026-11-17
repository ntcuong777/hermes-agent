"""``transform_status_bar_fields``: plugin-supplied, opt-in status-bar segments on the ``session.usage`` frame.

Core knows no segment by name. It dispatches the hook where the frame's bar fields are built (embedded ticker
and shared gateway), keeps valid rows whose id the bar lists, and carries them as ``Usage.status_fields``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway import session_usage
from hermes_cli import plugins
from tui_gateway import server, status_bar_fields as sbf


def _agent(**over):
    base = dict(model="m", provider="p", base_url=None, session_api_calls=1, session_id="sess-1",
                context_compressor=None)
    return SimpleNamespace(**{**base, **over})


@pytest.fixture
def manager(monkeypatch):
    mgr = plugins.PluginManager()
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: mgr)
    return mgr


def _register(mgr, callback):
    mgr._hooks.setdefault(sbf.HOOK, []).append(callback)


def _bar(monkeypatch, *fields):
    cfg = {"display": {"status_bar": {"fields": list(fields)}}}
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: cfg)
    monkeypatch.setattr(server, "_load_cfg_raw", lambda: cfg)


def test_the_hook_is_a_valid_hook():
    assert sbf.HOOK in plugins.VALID_HOOKS


# -- dispatch ------------------------------------------------------------------------------------------------


def test_a_plugin_row_for_a_listed_id_reaches_the_frame(manager, monkeypatch):
    seen = {}

    def hook(fields, session_id, agent, wanted):
        seen.update(fields=fields, session_id=session_id, agent=agent, wanted=wanted)
        return [*fields, {"id": "cost", "text": "$1.00", "tone": "warn", "min_cols": 70}]

    _register(manager, hook)
    _bar(monkeypatch, "model", "Cost")
    agent = _agent()
    out = sbf.status_bar_fields(agent)
    assert out == {"status_fields": [{"id": "cost", "text": "$1.00", "tone": "warn", "min_cols": 70}]}
    assert seen == {"fields": [], "session_id": "sess-1", "agent": agent, "wanted": ("model", "cost")}


def test_both_call_sites_carry_the_rows(manager, monkeypatch):
    _register(manager, lambda fields, **_: [{"id": "cost", "text": "$1.00"}])
    _bar(monkeypatch, "cost")
    assert server._get_usage(_agent())["status_fields"] == [{"id": "cost", "text": "$1.00"}]
    stats = session_usage.usage_stats(_agent())
    assert session_usage.tui_usage(stats)["status_fields"] == [{"id": "cost", "text": "$1.00"}]
    assert session_usage.usage_frame(stats)["usage"]["status_fields"] == [{"id": "cost", "text": "$1.00"}]


def test_plugins_chain_each_sees_the_rows_of_the_one_before(manager, monkeypatch):
    _register(manager, lambda fields, **_: [*fields, {"id": "a", "text": "A"}])
    _register(manager, lambda fields, **_: [*fields, {"id": "b", "text": f"B after {len(fields)}"}])
    _bar(monkeypatch, "a", "b")
    assert [r["text"] for r in sbf.status_bar_fields(_agent())["status_fields"]] == ["A", "B after 1"]


# -- nothing changes -----------------------------------------------------------------------------------------


def test_no_plugin_changes_nothing(manager, monkeypatch):
    _bar(monkeypatch, "cost", "quota")
    assert sbf.status_bar_fields(_agent()) == {}
    assert "status_fields" not in server._get_usage(_agent())
    assert "status_fields" not in session_usage.tui_usage(session_usage.usage_stats(_agent()))


def test_the_default_bar_dispatches_nothing(manager, monkeypatch):
    calls = []
    _register(manager, lambda fields, **_: calls.append(1) or [{"id": "cost", "text": "$1"}])
    _bar(monkeypatch)  # no display.status_bar.fields: the default bar
    assert sbf.status_bar_fields(_agent()) == {}
    assert calls == []


def test_a_bar_that_lists_other_names_gets_no_row_it_did_not_ask_for(manager, monkeypatch):
    _register(manager, lambda fields, **_: [{"id": "cost", "text": "$1"}])
    _bar(monkeypatch, "model", "context_pct")
    assert sbf.status_bar_fields(_agent()) == {}


def test_a_missing_or_malformed_config_dispatches_nothing(manager, monkeypatch):
    calls = []
    _register(manager, lambda fields, **_: calls.append(1) or [{"id": "cost", "text": "$1"}])
    for cfg in ({}, {"display": None}, "x", None):
        assert sbf.status_bar_fields(_agent(), lambda cfg=cfg: cfg) == {}
    assert calls == []


def test_an_unlisted_id_is_dropped(manager, monkeypatch):
    _register(manager, lambda fields, **_: [{"id": "cost", "text": "$1"}, {"id": "other", "text": "x"}])
    _bar(monkeypatch, "other")
    assert sbf.status_bar_fields(_agent()) == {"status_fields": [{"id": "other", "text": "x"}]}


# -- a failing plugin ----------------------------------------------------------------------------------------


def test_a_raising_plugin_does_not_break_the_frame(manager, monkeypatch):
    def boom(fields, **_):
        raise RuntimeError("plugin exploded")

    _register(manager, boom)
    _register(manager, lambda fields, **_: [*fields, {"id": "cost", "text": "$1"}])
    _bar(monkeypatch, "cost")
    assert server._get_usage(_agent())["status_fields"] == [{"id": "cost", "text": "$1"}]  # the next plugin still lands
    assert session_usage.usage_stats(_agent())["api_calls"] == 1


def test_a_failing_config_read_never_breaks_usage(monkeypatch):
    def boom():
        raise OSError("config unreadable")

    monkeypatch.setattr("hermes_cli.config.load_config_readonly", boom)
    assert sbf.status_bar_fields(_agent()) == {}
    assert session_usage.usage_stats(_agent())["api_calls"] == 1


@pytest.mark.parametrize("bad", ["text", 5, {"id": "cost", "text": "x"}, [None, 3, "x"]])
def test_a_garbage_return_is_ignored(manager, monkeypatch, bad):
    _register(manager, lambda fields, **_: bad)
    _bar(monkeypatch, "cost")
    assert sbf.status_bar_fields(_agent()) == {}


# -- validation ----------------------------------------------------------------------------------------------


def test_rows_are_validated_not_repaired():
    wanted = ("cost", "quota", "bad id", "x")
    rows = [
        {"id": "cost", "text": "  $1\x1b[31m  ", "tone": "nope", "min_cols": True},  # escape stripped, bad extras dropped
        {"id": "cost", "text": "dup"},  # first of an id wins
        {"id": "quota", "text": "", "tone": "bad"},  # no text, no row
        {"id": "Quota", "text": "case"},  # ids are lowercase
        {"id": "bad id", "text": "space"},
        {"id": "x", "text": "y" * 200, "tone": "good", "min_cols": 76},
        {"id": "x", "text": 5},
        "junk",
    ]
    assert sbf.clean_fields(rows, wanted) == [
        {"id": "cost", "text": "$1[31m"},
        {"id": "x", "text": "y" * sbf.MAX_TEXT, "tone": "good", "min_cols": 76},
    ]


def test_min_cols_must_be_a_sane_int():
    for bad in (-1, 10_000, 1.5, "60"):
        assert "min_cols" not in sbf.clean_fields([{"id": "x", "text": "t", "min_cols": bad}], ("x",))[0]


def test_the_row_count_is_capped():
    ids = tuple(f"f{i}" for i in range(20))
    rows = [{"id": i, "text": "t"} for i in ids]
    assert len(sbf.clean_fields(rows, ids)) == sbf.MAX_FIELDS
