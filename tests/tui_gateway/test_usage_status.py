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


# ── a resumed session keeps counting (cost carried over from earlier runs) ──────


class _Db:
    """The slice of SessionDB the bar reads: get_session (which drains queued deltas)."""

    def __init__(self, rows=None, *, fail=False):
        self.rows = rows or {}
        self.fail = fail
        self.reads = 0

    def get_session(self, session_id):
        self.reads += 1
        if self.fail:
            raise RuntimeError("state.db is locked")
        return self.rows.get(session_id)


def _row(cost, *, parent=None, end_reason=None, status="estimated", **extra):
    return {
        "estimated_cost_usd": cost,
        "parent_session_id": parent,
        "end_reason": end_reason,
        "cost_status": status,
        "source": "tui",
        "model_config": None,
        **extra,
    }


def _agent(db, *, session_id="s3", counter=0.0, status="unknown"):
    a = _Agent()
    a._session_db = db
    a.session_id = session_id
    a.session_estimated_cost_usd = counter
    a.session_cost_status = status
    return a


def test_a_resumed_session_starts_from_what_it_already_cost():
    agent = _agent(_Db({"s3": _row(1.25)}))
    assert usage_status.cost_fields(agent) == {
        "cost_usd": 1.25,
        "cost_status": "estimated",
    }


def test_new_spend_adds_to_the_carried_cost_and_the_db_is_read_once():
    db = _Db({"s3": _row(1.0)})
    agent = _agent(db)
    usage_status.cost_fields(agent)
    agent.session_estimated_cost_usd = 0.5
    agent.session_cost_status = "estimated"
    assert usage_status.cost_fields(agent)["cost_usd"] == 1.5
    agent.session_estimated_cost_usd = 0.75
    assert usage_status.cost_fields(agent)["cost_usd"] == 1.75
    assert db.reads == 1


def test_spend_this_process_already_wrote_is_not_counted_twice():
    # First seen mid-turn: the counter holds 0.3 and the db (drained on read) already has it.
    agent = _agent(_Db({"s3": _row(1.3)}), counter=0.3, status="estimated")
    assert usage_status.cost_fields(agent)["cost_usd"] == 1.3
    agent.session_estimated_cost_usd = 0.8
    assert usage_status.cost_fields(agent)["cost_usd"] == 1.8


def test_the_whole_compression_lineage_counts():
    db = _Db(
        {
            "s1": _row(0.3, end_reason="compression"),
            "s2": _row(0.5, parent="s1", end_reason="compression"),
            "s3": _row(0.2, parent="s2"),
        }
    )
    assert usage_status.cost_fields(_agent(db))["cost_usd"] == 1.0


def test_a_branch_starts_from_the_cost_recorded_at_the_fork():
    # The parent went on spending after the fork (now 9.0); the branch copied 5.0 and continues.
    db = _Db(
        {
            "s2": _row(9.0),
            "s3": _row(
                0.2,
                parent="s2",
                model_config={"_branched_from": "s2", "_cost_at_fork_usd": 5.0},
            ),
        }
    )
    assert usage_status.cost_fields(_agent(db))["cost_usd"] == 5.2


def test_a_branch_without_a_snapshot_copies_the_parents_cost_now():
    # Rows written by the classic CLI or older builds carry no snapshot: the parent's lineage total.
    db = _Db(
        {
            "s1": _row(1.0, end_reason="compression"),
            "s2": _row(4.0, parent="s1"),
            "s3": _row(0.2, parent="s2", model_config={"_branched_from": "s2"}),
        }
    )
    assert usage_status.cost_fields(_agent(db))["cost_usd"] == 5.2


def test_a_branch_of_a_branch_counts_each_fork_once():
    db = _Db(
        {
            "s1": _row(1.0),
            "s2": _row(
                2.0,
                parent="s1",
                model_config={"_branched_from": "s1", "_cost_at_fork_usd": 1.0},
            ),
            "s3": _row(
                0.5,
                parent="s2",
                model_config={"_branched_from": "s2", "_cost_at_fork_usd": 3.0},
            ),
        }
    )
    assert usage_status.cost_fields(_agent(db))["cost_usd"] == 3.5


def test_a_compressed_branch_counts_its_fork_once():
    # s3 is the branch, s4 its compression continuation (inheriting s3's model_config verbatim).
    config = {"_branched_from": "s2", "_cost_at_fork_usd": 5.0}
    db = _Db(
        {
            "s2": _row(9.0),
            "s3": _row(0.3, parent="s2", end_reason="compression", model_config=config),
            "s4": _row(0.2, parent="s3", model_config=config),
        }
    )
    assert usage_status.cost_fields(_agent(db, session_id="s4"))["cost_usd"] == 5.5


def test_a_branch_whose_parent_is_gone_keeps_its_own_cost():
    db = _Db({"s3": _row(0.2, parent="s2", model_config={"_branched_from": "s2"})})
    assert usage_status.cost_fields(_agent(db))["cost_usd"] == 0.2


def test_a_garbage_snapshot_falls_back_to_the_parents_cost_now():
    db = _Db(
        {
            "s2": _row(4.0),
            "s3": _row(
                0.2,
                parent="s2",
                model_config={"_branched_from": "s2", "_cost_at_fork_usd": "lots"},
            ),
        }
    )
    assert usage_status.cost_fields(_agent(db))["cost_usd"] == 4.2


def test_a_parent_that_did_not_end_by_compression_is_not_part_of_the_session():
    db = _Db({"s2": _row(5.0, end_reason="user_exit"), "s3": _row(0.2, parent="s2")})
    assert usage_status.cost_fields(_agent(db))["cost_usd"] == 0.2


def test_a_parent_cycle_ends_the_walk():
    db = _Db(
        {
            "s2": _row(1.0, parent="s3", end_reason="compression"),
            "s3": _row(1.0, parent="s2", end_reason="compression"),
        }
    )
    assert usage_status.cost_fields(_agent(db))["cost_usd"] == 2.0


def test_a_session_with_no_row_yet_costs_only_what_it_spent():
    agent = _agent(_Db({}), counter=0.4, status="estimated")
    assert usage_status.cost_fields(agent) == {
        "cost_usd": 0.4,
        "cost_status": "estimated",
    }


def test_the_agents_own_status_wins_over_the_stored_one():
    agent = _agent(
        _Db({"s3": _row(1.0, status="estimated")}), counter=0.1, status="actual"
    )
    assert usage_status.cost_fields(agent)["cost_status"] == "actual"


def test_an_unreadable_db_falls_back_to_the_counter_and_is_retried():
    db = _Db({"s3": _row(1.0)}, fail=True)
    agent = _agent(db, counter=0.4, status="estimated")
    assert usage_status.cost_fields(agent)["cost_usd"] == 0.4
    db.fail = False
    # The stored total already holds this process's 0.4 (get_session drains queued deltas).
    assert usage_status.cost_fields(agent)["cost_usd"] == 1.0


def test_an_agent_with_no_db_or_session_id_is_unchanged():
    assert usage_status.cost_fields(_Agent())["cost_usd"] == 0.4231
    assert (
        usage_status.cost_fields(_agent(None, counter=0.2, status="estimated"))[
            "cost_usd"
        ]
        == 0.2
    )


def test_get_usage_carries_a_resumed_sessions_cost(monkeypatch, _isolated_cache):
    monkeypatch.setattr(server, "_load_cfg_raw", lambda: _cfg("cost"))
    usage = server._get_usage(_agent(_Db({"s3": _row(2.0)})))
    assert usage["cost_usd"] == 2.0 and usage["cost_status"] == "estimated"


def test_a_marker_naming_another_row_is_inherited_not_a_fork():
    # A compression child of a branch carries its branch's marker verbatim: it names the branch's
    # origin (s0), not the compressed parent (s2), so the chain must continue through it.
    db = _Db(
        {
            "s2": _row(0.5, end_reason="compression", parent="s0"),
            "s3": _row(0.2, parent="s2", model_config='{"_branched_from": "s0"}'),
        }
    )
    assert usage_status.cost_fields(_agent(db))["cost_usd"] == 0.7


def test_a_reset_child_is_another_conversation():
    db = _Db(
        {
            "s2": _row(5.0, end_reason="compression"),
            "s3": _row(0.2, parent="s2", model_config={"_reset_from": "s2"}),
        }
    )
    assert usage_status.cost_fields(_agent(db))["cost_usd"] == 0.2


def test_a_real_session_db_round_trip(tmp_path, monkeypatch):
    """The columns the fakes assume exist, and queued deltas are drained by the read."""
    from hermes_state import SessionDB

    db = SessionDB(tmp_path / "state.db")
    db.create_session("s1", "tui")
    db.queue_token_counts(
        "s1", estimated_cost_usd=0.5, cost_status="estimated", api_call_count=1
    )
    db.end_session("s1", "compression")
    db.create_session("s2", "tui", parent_session_id="s1")
    db.queue_token_counts(
        "s2", estimated_cost_usd=0.25, cost_status="estimated", api_call_count=1
    )
    agent = _agent(db, session_id="s2")
    assert usage_status.cost_fields(agent) == {
        "cost_usd": 0.75,
        "cost_status": "estimated",
    }


def test_a_session_that_made_calls_but_has_no_price_says_unknown_not_blank():
    """Opus 5.5 turns with no price (B17): the bar must show n/a, not nothing, so a missing cost is visible."""

    class Unpriced(_Agent):
        session_estimated_cost_usd = 0.0
        session_cost_status = "unknown"
        session_api_calls = 3

    assert usage_status.cost_fields(Unpriced()) == {"cost_status": "unpriced"}
