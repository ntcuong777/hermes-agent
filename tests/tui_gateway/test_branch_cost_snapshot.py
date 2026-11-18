"""A branch copies what its parent had cost at the fork and continues from there.

The snapshot lives in the child's ``model_config`` (not in ``estimated_cost_usd``, so analytics
that sum the column do not count the parent's spend twice). It is a snapshot because the parent
may keep spending after the fork; the branch must not inherit that.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pathlib import Path

from tests.fixtures.storage_seed import seed_session, seed_usage


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
    raw = db.sessions.get(key).model_config
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


def _rows(db):
    """The row reader ``lineage_cost`` takes: a dict per session, as the old store returned it."""
    return SimpleNamespace(get_session=lambda sid: (
        record.as_session_row() if (record := db.sessions.get(sid)) is not None else None))


def test_the_branch_row_records_the_parents_cost_at_the_fork(tmp_path, monkeypatch):
    from persistence_assembly import open_persistence, profile_scope
    from tui_gateway import server

    home = _home(tmp_path, monkeypatch)
    with open_persistence(profile_scope(home=home)) as db:
        seed_session(db, "parent", "desktop", model="test-model")
        seed_usage(db, "parent", estimated_cost_usd=1.5, api_call_count=1)
        _branch(server, db, "child", "parent", tmp_path)

        config = _config(db, "child")
        assert config["_branched_from"] == "parent"
        assert config["_cost_at_fork_usd"] == 1.5
        # The snapshot is not spend: the column that analytics sum stays the branch's own.
        assert not db.sessions.get("child").estimated_cost_usd


def test_the_snapshot_includes_the_parents_compression_ancestors(tmp_path, monkeypatch):
    from persistence_assembly import open_persistence, profile_scope
    from tui_gateway import server

    home = _home(tmp_path, monkeypatch)
    with open_persistence(profile_scope(home=home)) as db:
        seed_session(db, "p1", "desktop", model="test-model")
        seed_usage(db, "p1", estimated_cost_usd=1.0, api_call_count=1)
        db.sessions.end("p1", "compression")
        seed_session(db, "p2", "desktop", model="test-model", parent_session_id="p1")
        seed_usage(db, "p2", estimated_cost_usd=0.5, api_call_count=1)
        _branch(server, db, "child", "p2", tmp_path)

        assert _config(db, "child")["_cost_at_fork_usd"] == 1.5


def test_a_parent_that_has_cost_nothing_records_no_snapshot(tmp_path, monkeypatch):
    from persistence_assembly import open_persistence, profile_scope
    from tui_gateway import server

    home = _home(tmp_path, monkeypatch)
    with open_persistence(profile_scope(home=home)) as db:
        seed_session(db, "parent", "desktop", model="test-model")
        _branch(server, db, "child", "parent", tmp_path)

        assert _config(db, "child") == {"_branched_from": "parent"}


def test_a_parent_that_cannot_be_read_still_gets_a_branch(tmp_path, monkeypatch):
    from persistence_assembly import open_persistence, profile_scope
    from tui_gateway import server, usage_status

    def boom(*_a, **_k):
        raise RuntimeError("state.db is locked")

    monkeypatch.setattr(usage_status, "lineage_cost", boom)
    home = _home(tmp_path, monkeypatch)
    with open_persistence(profile_scope(home=home)) as db:
        seed_session(db, "parent", "desktop", model="test-model")
        _branch(server, db, "child", "parent", tmp_path)

        assert _config(db, "child") == {"_branched_from": "parent"}


def test_a_resumed_branch_carries_the_parents_cost_at_the_fork_then_counts_on(
    tmp_path, monkeypatch
):
    """Fork, let the parent spend more, then resume the branch: it carries the snapshot."""
    from persistence_assembly import open_persistence, profile_scope
    from tui_gateway import server, usage_status

    home = _home(tmp_path, monkeypatch)
    with open_persistence(profile_scope(home=home)) as db:
        seed_session(db, "parent", "desktop", model="test-model")
        seed_usage(db, "parent", estimated_cost_usd=2.0, cost_status="estimated", api_call_count=1)
        _branch(server, db, "child", "parent", tmp_path)
        seed_usage(db, "parent", estimated_cost_usd=7.0, cost_status="estimated", api_call_count=1)
        seed_usage(db, "child", estimated_cost_usd=0.25, cost_status="estimated", api_call_count=1)

        assert usage_status.lineage_cost(_rows(db), "child")[0] == pytest.approx(2.25)
