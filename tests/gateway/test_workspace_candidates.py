"""``candidate_workspaces``: the profile's directory list behind ``/cwd list`` and the Discord
type-ahead. Projects lead, repo roots follow, dedup keeps the first spelling, missing directories
drop, the cap holds, and every data-source failure degrades instead of raising (fail-soft contract).
"""
import contextlib

import pytest

from gateway.workspace_candidates import candidate_workspaces, match_workspace_prefix


@pytest.fixture
def real_dirs(tmp_path):
    a, b = tmp_path / 'alpha', tmp_path / 'beta'
    a.mkdir(), b.mkdir()
    return a, b


def _patch_sources(monkeypatch, *, projects=(), repos=()):
    """Stub the module's data-source boundary: ``_candidate_paths`` -> (project paths, repo roots)."""
    import gateway.workspace_candidates as mod
    monkeypatch.setattr(mod, '_candidate_paths', lambda db: (list(projects), list(repos)))


def test_projects_lead_and_duplicates_drop(tmp_path, real_dirs, monkeypatch):
    a, b = real_dirs
    _patch_sources(monkeypatch, projects=[str(a), str(b), str(a)], repos=[str(b), str(b)])
    assert candidate_workspaces() == [str(a), str(b)]


def test_missing_directories_drop(tmp_path, monkeypatch):
    _patch_sources(monkeypatch, projects=[str(tmp_path / 'gone')], repos=[])
    assert candidate_workspaces() == []


def test_cap_holds(monkeypatch, tmp_path):
    dirs = []
    for i in range(40):
        d = tmp_path / f'd{i}'
        d.mkdir()
        dirs.append(str(d))
    _patch_sources(monkeypatch, projects=dirs)
    assert len(candidate_workspaces()) == 25


def test_candidate_paths_extracts_project_and_folder_paths(tmp_path, real_dirs, monkeypatch):
    """The real projects.db boundary: primary_path plus folder paths, dicts flattened to strings."""
    a, b = real_dirs
    import hermes_cli.projects_db as pdb
    import gateway.workspace_candidates as mod

    class _Project:
        def to_dict(self):
            return {"primary_path": str(a), "folders": [{"path": str(b)}, {"path": str(a)}]}

    monkeypatch.setattr(pdb, 'connect_closing', lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(pdb, 'list_projects', lambda conn: [_Project()])
    projects, _repos = mod._candidate_paths(None)
    assert projects == [str(a), str(b)]


def test_source_failures_degrade_to_what_survives(tmp_path, real_dirs, monkeypatch):
    """projects.db raising must not hide the repos (fail-soft contract): the repo payload still
    reaches the result."""
    a, _b = real_dirs
    import hermes_cli.projects_db as pdb
    import tui_gateway.server as gw

    def _boom():
        raise RuntimeError('db locked')
    monkeypatch.setattr(pdb, 'connect_closing', _boom)
    monkeypatch.setattr(gw, '_discover_repos_payload', lambda db, **kw: [{'root': str(a), 'label': 'a'}])
    assert candidate_workspaces() == [str(a)]


def test_prefix_match_on_path_and_basename():
    matches, note = match_workspace_prefix(['/home/me/code/website', '/srv/data'], 'code/web')
    assert (note, matches) == ('unique', ['/home/me/code/website'])
    assert match_workspace_prefix([], 'x') == ([], '')
