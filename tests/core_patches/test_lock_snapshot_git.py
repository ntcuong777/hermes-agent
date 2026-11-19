"""The lock snapshot of a recorded patch is the target at the recorded UPSTREAM commit, not the working tree
(hermes_cli/core_patches_ast.py write_lock_entry; schema field ``snapshot_source``)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from hermes_cli import core_patches_ast as cpa

COMMITTED = "def f():\n    return 1\n"
EDITED = "def f():\n    return 2\n"


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c",
                          "commit.gpgsign=false", *args], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    """A checkout with `pkgx.py` committed as COMMITTED, then edited in the working tree to EDITED."""
    repo = tmp_path / "root"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "pkgx.py").write_text(COMMITTED, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "upstream")
    sha = _git(repo, "rev-parse", "HEAD")
    (repo / "pkgx.py").write_text(EDITED, encoding="utf-8")
    return repo, sha


def _record(plugin_dir: Path, repo: Path, commit: str) -> dict:
    cpa.write_lock_entry(plugin_dir, "f", "pkgx:f", repo, provenance={"upstream_ref": {"commit": commit}})
    return cpa.read_lock(plugin_dir)["f"]


def test_the_snapshot_is_the_committed_function_and_the_source_is_named(tmp_path):
    repo, sha = _repo(tmp_path)
    plugin_dir = tmp_path / "plugin"
    entry = _record(plugin_dir, repo, sha)
    assert cpa.snapshot_text(plugin_dir, entry) == COMMITTED
    assert entry["snapshot_source"] == "git:" + sha[:12]
    assert entry["snapshot_sha256"] == hashlib.sha256(COMMITTED.encode("utf-8")).hexdigest()


def test_a_commit_that_does_not_exist_falls_back_to_the_working_tree(tmp_path):
    repo, _ = _repo(tmp_path)
    plugin_dir = tmp_path / "plugin"
    entry = _record(plugin_dir, repo, "0" * 40)
    assert cpa.snapshot_text(plugin_dir, entry) == EDITED
    assert entry["snapshot_source"] == "working-tree"


def test_an_unknown_commit_falls_back_to_the_working_tree_without_git(tmp_path):
    repo, _ = _repo(tmp_path)
    plugin_dir = tmp_path / "plugin"
    entry = _record(plugin_dir, repo, "unknown")
    assert cpa.snapshot_text(plugin_dir, entry) == EDITED
    assert entry["snapshot_source"] == "working-tree"


def test_a_lock_without_snapshot_source_still_reads_and_its_snapshot_still_resolves(tmp_path):
    repo, sha = _repo(tmp_path)
    plugin_dir = tmp_path / "plugin"
    _record(plugin_dir, repo, sha)
    lock = plugin_dir / cpa.LOCK_FILE
    data = json.loads(lock.read_text(encoding="utf-8"))
    del data["patches"]["f"]["snapshot_source"]
    lock.write_text(json.dumps(data), encoding="utf-8")
    entry = cpa.read_lock(plugin_dir)["f"]
    assert cpa.snapshot_text(plugin_dir, entry) == COMMITTED
