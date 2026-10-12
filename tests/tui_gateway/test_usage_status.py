"""Session cost and the active account's quota windows in the TUI status bar."""

from __future__ import annotations

from datetime import timedelta

import pytest

from agent import account_usage_cache as cache
from agent.account_usage import AccountUsageSnapshot, AccountUsageWindow, _utc_now
from tui_gateway import server, usage_status


class _Agent:
    model = "m"
    provider = "openai-codex"
    base_url = None
    session_estimated_cost_usd = 0.4231
    session_cost_status = "estimated"


def _snapshot(*windows, age_s=0.0, provider="openai-codex"):
    return AccountUsageSnapshot(
        provider=provider,
        source="usage_api",
        fetched_at=_utc_now() - timedelta(seconds=age_s),
        windows=tuple(windows),
    )


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch):
    monkeypatch.setattr(cache, "_snapshots", {})
    monkeypatch.setattr(cache, "_started", {})
    asked: list = []
    monkeypatch.setattr(
        cache,
        "refresh_account_usage_async",
        lambda providers: asked.append(list(providers)) or [],
    )
    return asked


# ── opt-in ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cfg",
    [{}, {"display": None}, {"display": {"status_bar": {"fields": []}}}, "x", None],
)
def test_nothing_is_wanted_without_a_field_list(cfg):
    assert not usage_status.bar_wants(cfg, "cost")
    assert not usage_status.bar_wants(cfg, "quota")


def test_a_listed_field_is_wanted_whatever_its_case():
    cfg = {"display": {"status_bar": {"fields": ["model", " Cost "]}}}
    assert usage_status.bar_wants(cfg, "cost")
    assert not usage_status.bar_wants(cfg, "quota")


# ── cost ─────────────────────────────────────────────────────────────────────


def test_cost_carries_the_session_total_and_the_agents_own_status():
    assert usage_status.cost_fields(_Agent()) == {
        "cost_usd": 0.4231,
        "cost_status": "estimated",
    }


def test_a_zero_cost_with_no_status_says_nothing():
    class Cold(_Agent):
        session_estimated_cost_usd = 0.0
        session_cost_status = "unknown"

    assert usage_status.cost_fields(Cold()) == {}


def test_a_subscription_that_bills_nothing_says_included():
    class Sub(_Agent):
        session_estimated_cost_usd = 0.0
        session_cost_status = "included"

    assert usage_status.cost_fields(Sub()) == {"cost_status": "included"}


def test_an_agent_without_the_attributes_is_not_an_error():
    assert usage_status.cost_fields(object()) == {}


# ── quota ────────────────────────────────────────────────────────────────────


def _remember(snapshot, **kw):
    cache.remember_account_usage(snapshot.provider, snapshot, **kw)


def test_quota_lists_the_account_windows_of_the_active_provider(_isolated_cache):
    _remember(
        _snapshot(
            AccountUsageWindow("Session", 12.34), AccountUsageWindow("Weekly", 60.0)
        )
    )
    assert usage_status.quota_fields(_Agent()) == {
        "quota": [
            {"label": "Session", "used_percent": 12.3},
            {"label": "Weekly", "used_percent": 60.0},
        ]
    }
    assert _isolated_cache == [
        ["openai-codex"]
    ]  # a background refresh was asked for, nothing fetched inline


def test_a_model_scoped_window_is_left_to_usage(_isolated_cache):
    _remember(
        _snapshot(
            AccountUsageWindow("Weekly", 10.0),
            AccountUsageWindow("Opus", 99.0, scope="model"),
        )
    )
    assert usage_status.quota_fields(_Agent()) == {
        "quota": [{"label": "Weekly", "used_percent": 10.0}]
    }


def test_a_window_without_a_percent_is_dropped(_isolated_cache):
    _remember(
        _snapshot(
            AccountUsageWindow("Weekly", None), AccountUsageWindow("Session", 5.0)
        )
    )
    assert usage_status.quota_fields(_Agent())["quota"] == [
        {"label": "Session", "used_percent": 5.0}
    ]


def test_another_providers_windows_are_never_shown(_isolated_cache):
    _remember(_snapshot(AccountUsageWindow("5h", 1.0), provider="anthropic"))
    assert usage_status.quota_fields(_Agent()) == {}  # the agent talks to openai-codex


def test_a_stale_snapshot_is_not_shown_as_live(_isolated_cache):
    _remember(
        _snapshot(
            AccountUsageWindow("Weekly", 10.0), age_s=cache.SNAPSHOT_STALE_AFTER_S + 60
        )
    )
    assert usage_status.quota_fields(_Agent()) == {}


def test_before_the_first_fetch_lands_the_bar_has_no_quota_and_the_refresh_is_asked(
    _isolated_cache,
):
    assert usage_status.quota_fields(_Agent()) == {}
    assert _isolated_cache == [["openai-codex"]]


def test_an_agent_with_no_provider_does_nothing(_isolated_cache):
    class NoProvider(_Agent):
        provider = ""

    assert usage_status.quota_fields(NoProvider()) == {}
    assert _isolated_cache == []


def test_a_failing_cache_never_breaks_usage(monkeypatch):
    def boom(_providers):
        raise RuntimeError("cache exploded")

    monkeypatch.setattr(cache, "refresh_account_usage_async", boom)
    assert usage_status.quota_fields(_Agent()) == {}


# ── through _get_usage ───────────────────────────────────────────────────────


def _cfg(*fields):
    return {"display": {"status_bar": {"fields": list(fields)}}}


def test_get_usage_omits_both_for_the_default_bar(monkeypatch, _isolated_cache):
    _remember(_snapshot(AccountUsageWindow("Weekly", 10.0)))
    monkeypatch.setattr(server, "_load_cfg_raw", lambda: {})
    usage = server._get_usage(_Agent())
    assert "cost_usd" not in usage and "quota" not in usage
    assert (
        _isolated_cache == []
    )  # no usage API is polled for a bar that does not show it


def test_get_usage_adds_what_the_bar_lists(monkeypatch, _isolated_cache):
    _remember(_snapshot(AccountUsageWindow("Weekly", 10.0)))
    monkeypatch.setattr(server, "_load_cfg_raw", lambda: _cfg("model", "cost", "quota"))
    usage = server._get_usage(_Agent())
    assert usage["cost_usd"] == 0.4231 and usage["cost_status"] == "estimated"
    assert usage["quota"] == [{"label": "Weekly", "used_percent": 10.0}]


def test_get_usage_adds_cost_without_polling_quota(monkeypatch, _isolated_cache):
    monkeypatch.setattr(server, "_load_cfg_raw", lambda: _cfg("cost"))
    usage = server._get_usage(_Agent())
    assert usage["cost_usd"] == 0.4231 and "quota" not in usage
    assert _isolated_cache == []
