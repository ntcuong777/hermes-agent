"""A killed checkpoint git (an ``add -A`` past its timeout) leaves ``indexes/<hash>.lock`` in the
store, and every later checkpoint of that workspace failed with "Unable to create ... .lock: File
exists" — forever, so /rollback had nothing to restore. An old lock inside the store is now cleared
and the git call retried once; a fresh one (possibly a live git) is left alone."""
import os
import time
from pathlib import Path

import pytest

from tools import checkpoint_manager as cm
from tools.checkpoint_manager import CheckpointManager


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    work = tmp_path / "project"
    work.mkdir()
    (work / "main.py").write_text("print('hello')\n")
    base = tmp_path / "checkpoints"
    monkeypatch.setattr(cm, "CHECKPOINT_BASE", base)
    mgr = CheckpointManager(enabled=True, max_snapshots=50)
    assert mgr.ensure_checkpoint(str(work), "first") is True
    locks = list(base.rglob("indexes/*"))
    index = next(p for p in locks if not p.name.endswith(".lock"))
    return mgr, work, Path(str(index) + ".lock")


def _age(path, seconds):
    old = time.time() - seconds
    os.utime(path, (old, old))


def _next_turn(mgr, work, text):
    mgr.new_turn()
    (work / "main.py").write_text(text)
    return mgr.ensure_checkpoint(str(work), text)


def test_a_stale_index_lock_is_cleared_and_the_checkpoint_lands(setup):
    mgr, work, lock = setup
    lock.write_text("")
    _age(lock, 3600)
    assert _next_turn(mgr, work, "print('two')\n") is True
    assert not lock.exists()


def test_a_fresh_lock_is_left_alone(setup):
    mgr, work, lock = setup
    lock.write_text("")
    assert _next_turn(mgr, work, "print('three')\n") is False
    assert lock.exists()


def test_a_lock_outside_the_store_is_never_touched(tmp_path):
    outside = tmp_path / "elsewhere" / "index.lock"
    outside.parent.mkdir()
    outside.write_text("")
    _age(outside, 3600)
    stderr = f"fatal: Unable to create '{outside}': File exists."
    assert cm._clear_stale_store_lock(stderr, tmp_path / "store") is False
    assert outside.exists()
