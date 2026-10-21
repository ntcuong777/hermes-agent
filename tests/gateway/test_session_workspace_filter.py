"""Workspace filter for the session pickers: ``session.list`` with ``cwd``.

A session belongs to the workspace its store key names: the main git repo root, else the
directory it started in. Listing with ``cwd`` keeps only that workspace, so a picker opened
in one project does not show every other project's sessions.
"""

from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace

import pytest

from hermes_state_sessions import rows_in_workspace, workspace_for_dir, workspace_key
from tui_gateway import server


def _git_repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    sub = path / "pkg"
    sub.mkdir()

    return path, sub


def test_rows_in_workspace_matches_store_key_and_ignores_trailing_slash():
    rows = [
        {"id": "a", "git_repo_root": "/work/repo", "cwd": "/work/repo/pkg"},
        {"id": "b", "git_repo_root": "", "cwd": "/work/scratch"},
        {"id": "c", "git_repo_root": "/work/other", "cwd": "/work/other"},
    ]

    assert [r["id"] for r in rows_in_workspace(rows, "/work/repo/")] == ["a"]
    assert [r["id"] for r in rows_in_workspace(rows, "/work/scratch")] == ["b"]
    assert [r["id"] for r in rows_in_workspace(rows, None)] == ["a", "b", "c"]


def test_workspace_for_dir_is_repo_root_inside_a_repo(tmp_path):
    repo, sub = _git_repo(tmp_path / "repo")

    assert workspace_for_dir(str(sub)) == str(repo.resolve())


def test_workspace_for_dir_is_the_directory_outside_a_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    assert workspace_for_dir(str(plain)) == str(plain.resolve())


class _StubDB:
    def __init__(self, rows):
        self.rows = rows

    def list_sessions_rich(self, **kwargs):
        return list(self.rows)


def test_legacy_session_list_with_cwd_keeps_only_that_workspace(monkeypatch, tmp_path):
    repo, sub = _git_repo(tmp_path / "repo")
    other = tmp_path / "other"
    other.mkdir()
    root = str(repo.resolve())
    rows = [
        {
            "id": "in-repo",
            "source": "tui",
            "started_at": 3,
            "git_repo_root": root,
            "cwd": str(sub),
        },
        {
            "id": "elsewhere",
            "source": "tui",
            "started_at": 2,
            "git_repo_root": "",
            "cwd": str(other),
        },
        {
            "id": "plain-here",
            "source": "tui",
            "started_at": 1,
            "git_repo_root": "",
            "cwd": str(sub),
        },
    ]
    monkeypatch.setattr(server, "_get_db", lambda: _StubDB(rows))

    resp = server.handle_request(
        {"id": "1", "method": "session.list", "params": {"cwd": str(sub)}}
    )
    ids = [s["id"] for s in resp["result"]["sessions"]]

    assert ids == ["in-repo"]


def test_legacy_session_list_without_cwd_is_unfiltered(monkeypatch, tmp_path):
    rows = [
        {
            "id": "a",
            "source": "tui",
            "started_at": 2,
            "git_repo_root": "/x",
            "cwd": "/x",
        },
        {"id": "b", "source": "tui", "started_at": 1, "git_repo_root": "", "cwd": "/y"},
    ]
    monkeypatch.setattr(server, "_get_db", lambda: _StubDB(rows))

    resp = server.handle_request({"id": "1", "method": "session.list", "params": {}})

    assert [s["id"] for s in resp["result"]["sessions"]] == ["a", "b"]


def test_canonical_list_rejects_cwd_with_title_or_blank_cwd():
    from gateway.session_controls import AuthorityConnection
    from hermes_state_runtime import RuntimeStoreError

    stub = SimpleNamespace(
        actor=SimpleNamespace(capabilities={"session:read"}), authority=None
    )

    for params in ({"cwd": "/x", "title": "t"}, {"cwd": "   "}, {"cwd": 7}):
        with pytest.raises(RuntimeStoreError) as exc:
            asyncio.run(AuthorityConnection.list_sessions(stub, None, params))

        assert exc.value.reason == "invalid_params"


def test_wire_params_accept_cwd():
    from tui_gateway.contracts.canonical_sessions import CanonicalListParams
    from tui_gateway.contracts.sessions import SessionListParams

    assert SessionListParams(cwd="/x").cwd == "/x"
    assert CanonicalListParams(cwd="/x").cwd == "/x"


def test_live_listing_rows_carry_the_workspace_columns(tmp_path):
    """The live feed filters on ``cwd`` / ``git_repo_root``; its row query must select them."""
    from gateway.session_live_listing import _store_facts
    from hermes_state import SessionDB

    db = SessionDB(tmp_path / "state.db")
    db.create_session("live-1", source="tui", cwd=str(tmp_path))

    rows, _pending = _store_facts(db, ["live-1"])

    assert rows["live-1"]["cwd"] == str(tmp_path)
    assert "git_repo_root" in rows["live-1"]
