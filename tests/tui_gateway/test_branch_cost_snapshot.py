"""A branch copies what its parent had cost at the fork and continues from there.

The snapshot lives in the child's ``model_config`` (not in ``estimated_cost_usd``, so analytics
that sum the column do not count the parent's spend twice). It is a snapshot because the parent
may keep spending after the fork; the branch must not inherit that.
"""

from __future__ import annotations

import json
from pathlib import Path


def _home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _branch(server, db, child, parent, tmp_path):
    server._persist_branch(
        db,
        child,
        parent,
        "Branch",
        [{"role": "user", "content": "hi"}],
        source="desktop",
        cwd=str(tmp_path),
        profile_name="default",
        model="test-model",
    )


def _config(db, key) -> dict:
    raw = db.get_session(key)["model_config"]
    return json.loads(raw) if isinstance(raw, str) else raw


def test_the_branch_row_records_the_parents_cost_at_the_fork(tmp_path, monkeypatch):
    from hermes_state import SessionDB
    from tui_gateway import server

    home = _home(tmp_path, monkeypatch)
    with SessionDB(home / "state.db") as db:
        db.create_session("parent", source="desktop", model="test-model")
        db.queue_token_counts("parent", estimated_cost_usd=1.5, api_call_count=1)
        _branch(server, db, "child", "parent", tmp_path)

        config = _config(db, "child")
        assert config["_branched_from"] == "parent"
        assert config["_cost_at_fork_usd"] == 1.5
        # The snapshot is not spend: the column that analytics sum stays the branch's own.
        assert not db.get_session("child")["estimated_cost_usd"]


def test_the_snapshot_includes_the_parents_compression_ancestors(tmp_path, monkeypatch):
    from hermes_state import SessionDB
    from tui_gateway import server

    home = _home(tmp_path, monkeypatch)
    with SessionDB(home / "state.db") as db:
        db.create_session("p1", source="desktop", model="test-model")
        db.queue_token_counts("p1", estimated_cost_usd=1.0, api_call_count=1)
        db.end_session("p1", "compression")
        db.create_session(
            "p2", source="desktop", model="test-model", parent_session_id="p1"
        )
        db.queue_token_counts("p2", estimated_cost_usd=0.5, api_call_count=1)
        _branch(server, db, "child", "p2", tmp_path)

        assert _config(db, "child")["_cost_at_fork_usd"] == 1.5


def test_a_parent_that_has_cost_nothing_records_no_snapshot(tmp_path, monkeypatch):
    from hermes_state import SessionDB
    from tui_gateway import server

    home = _home(tmp_path, monkeypatch)
    with SessionDB(home / "state.db") as db:
        db.create_session("parent", source="desktop", model="test-model")
        _branch(server, db, "child", "parent", tmp_path)

        assert _config(db, "child") == {"_branched_from": "parent"}


def test_a_parent_that_cannot_be_read_still_gets_a_branch(tmp_path, monkeypatch):
    from hermes_state import SessionDB
    from tui_gateway import server, usage_status

    def boom(*_a, **_k):
        raise RuntimeError("state.db is locked")

    monkeypatch.setattr(usage_status, "lineage_cost", boom)
    home = _home(tmp_path, monkeypatch)
    with SessionDB(home / "state.db") as db:
        db.create_session("parent", source="desktop", model="test-model")
        _branch(server, db, "child", "parent", tmp_path)

        assert _config(db, "child") == {"_branched_from": "parent"}


def test_the_bar_shows_the_branch_at_the_parents_cost_then_counts_on(
    tmp_path, monkeypatch
):
    """Fork, let the parent spend more, then resume the branch: it carries the snapshot."""
    from hermes_state import SessionDB
    from tui_gateway import server, usage_status

    home = _home(tmp_path, monkeypatch)
    with SessionDB(home / "state.db") as db:
        db.create_session("parent", source="desktop", model="test-model")
        db.queue_token_counts(
            "parent", estimated_cost_usd=2.0, cost_status="estimated", api_call_count=1
        )
        _branch(server, db, "child", "parent", tmp_path)
        db.queue_token_counts(
            "parent", estimated_cost_usd=7.0, cost_status="estimated", api_call_count=1
        )
        db.queue_token_counts(
            "child", estimated_cost_usd=0.25, cost_status="estimated", api_call_count=1
        )

        class Agent:
            _session_db = db
            session_id = "child"
            session_estimated_cost_usd = 0.0
            session_cost_status = "unknown"

        assert usage_status.cost_fields(Agent()) == {
            "cost_usd": 2.25,
            "cost_status": "estimated",
        }
