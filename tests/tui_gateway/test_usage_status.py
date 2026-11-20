"""Session cost lineage (``tui_gateway/usage_status.py::lineage_cost``): what a session, the compression chain
it continues and the branch it began as have cost. The status bar's cost segment is the quota plugin's now
(hook ``transform_status_bar_fields``); its formatting and carried-cost tests live in that plugin."""

from __future__ import annotations

import pytest

from tui_gateway import usage_status


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








def test_the_whole_compression_lineage_counts():
    db = _Db(
        {
            "s1": _row(0.3, end_reason="compression"),
            "s2": _row(0.5, parent="s1", end_reason="compression"),
            "s3": _row(0.2, parent="s2"),
        }
    )
    assert usage_status.lineage_cost(db, "s3")[0] == pytest.approx(1.0)


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
    assert usage_status.lineage_cost(db, "s3")[0] == pytest.approx(5.2)


def test_a_branch_without_a_snapshot_copies_the_parents_cost_now():
    # Rows written by the classic CLI or older builds carry no snapshot: the parent's lineage total.
    db = _Db(
        {
            "s1": _row(1.0, end_reason="compression"),
            "s2": _row(4.0, parent="s1"),
            "s3": _row(0.2, parent="s2", model_config={"_branched_from": "s2"}),
        }
    )
    assert usage_status.lineage_cost(db, "s3")[0] == pytest.approx(5.2)


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
    assert usage_status.lineage_cost(db, "s3")[0] == pytest.approx(3.5)


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
    assert usage_status.lineage_cost(db, "s4")[0] == pytest.approx(5.5)


def test_a_branch_whose_parent_is_gone_keeps_its_own_cost():
    db = _Db({"s3": _row(0.2, parent="s2", model_config={"_branched_from": "s2"})})
    assert usage_status.lineage_cost(db, "s3")[0] == pytest.approx(0.2)


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
    assert usage_status.lineage_cost(db, "s3")[0] == pytest.approx(4.2)


def test_a_parent_that_did_not_end_by_compression_is_not_part_of_the_session():
    db = _Db({"s2": _row(5.0, end_reason="user_exit"), "s3": _row(0.2, parent="s2")})
    assert usage_status.lineage_cost(db, "s3")[0] == pytest.approx(0.2)


def test_a_parent_cycle_ends_the_walk():
    db = _Db(
        {
            "s2": _row(1.0, parent="s3", end_reason="compression"),
            "s3": _row(1.0, parent="s2", end_reason="compression"),
        }
    )
    assert usage_status.lineage_cost(db, "s3")[0] == pytest.approx(2.0)












def test_a_marker_naming_another_row_is_inherited_not_a_fork():
    # A compression child of a branch carries its branch's marker verbatim: it names the branch's
    # origin (s0), not the compressed parent (s2), so the chain must continue through it.
    db = _Db(
        {
            "s2": _row(0.5, end_reason="compression", parent="s0"),
            "s3": _row(0.2, parent="s2", model_config='{"_branched_from": "s0"}'),
        }
    )
    assert usage_status.lineage_cost(db, "s3")[0] == pytest.approx(0.7)


def test_a_reset_child_is_another_conversation():
    db = _Db(
        {
            "s2": _row(5.0, end_reason="compression"),
            "s3": _row(0.2, parent="s2", model_config={"_reset_from": "s2"}),
        }
    )
    assert usage_status.lineage_cost(db, "s3")[0] == pytest.approx(0.2)


def test_a_real_session_db_round_trip(tmp_path, monkeypatch):
    """The columns the fakes assume exist, and queued deltas are drained by the read."""
    from types import SimpleNamespace

    from persistence_assembly import open_persistence, profile_scope
    from tests.fixtures.storage_seed import seed_session, seed_usage

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    with open_persistence(profile_scope(home=home)) as store:
        seed_session(store, "s1", "tui")
        seed_usage(store, "s1", estimated_cost_usd=0.5, cost_status="estimated", api_call_count=1)
        store.sessions.end("s1", "compression")
        seed_session(store, "s2", "tui", parent_session_id="s1")
        seed_usage(store, "s2", estimated_cost_usd=0.25, cost_status="estimated", api_call_count=1)
        rows = SimpleNamespace(get_session=lambda sid: (
            record.as_session_row() if (record := store.sessions.get(sid)) is not None else None))
        assert usage_status.lineage_cost(rows, "s2") == (0.75, "estimated")



class _Persistence:
    """The handle an agent holds as ``_session_db``: no ``get_session``, rows behind ``sessions.get(...)``."""

    def __init__(self, rows):
        self.sessions = self
        self._rows = rows

    def get(self, session_id):
        row = self._rows.get(session_id)
        return None if row is None else type("Record", (), {"as_session_row": lambda _self: row})()


def test_a_persistence_handle_reads_the_stored_lineage_cost():
    """Regression: the handle has no get_session, so the carried cost read raised, was logged at debug and the bar
    restarted at this process's counter (a long session showed $3.30 instead of $214)."""
    handle = _Persistence({"tip": _row(2.0, parent="root"), "root": _row(214.0, end_reason="compression")})
    assert usage_status.lineage_cost(handle, "tip") == (216.0, "estimated")


def test_a_persistence_handle_with_an_unknown_session_costs_nothing():
    assert usage_status.lineage_cost(_Persistence({}), "x") == (0.0, "")


def test_a_store_with_no_known_read_shape_raises_instead_of_reading_zero():
    with pytest.raises(AttributeError):
        usage_status.lineage_cost(object(), "x")
