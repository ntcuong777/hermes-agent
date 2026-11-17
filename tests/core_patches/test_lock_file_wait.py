"""A writer waiting on a busy lock file gives up after LOCK_WAIT_S instead of blocking forever
(hermes_cli/core_patches_ast.py _lock_file_held)."""

from __future__ import annotations

import threading
import time

import pytest

from hermes_cli import core_patches_ast as cpa


def test_a_busy_lock_file_raises_lock_file_busy_after_the_wait_instead_of_blocking(tmp_path, monkeypatch):
    fcntl = pytest.importorskip("fcntl")
    monkeypatch.setattr(cpa, "LOCK_WAIT_S", 0.3, raising=False)  # absent before the fix: it then blocks
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    holder = open(plugin_dir / (cpa.LOCK_FILE + ".lock"), "a", encoding="utf-8")
    fcntl.flock(holder, fcntl.LOCK_EX)  # another writer holds it (a second open file description)
    outcome = []

    def attempt():
        try:
            with cpa._lock_file_held(plugin_dir):
                outcome.append("held")
        except cpa.CorePatchFileError as exc:
            outcome.append(str(exc))
    worker = threading.Thread(target=attempt, daemon=True)
    try:
        started = time.monotonic()
        worker.start()
        worker.join(3.0)
        assert not worker.is_alive() and time.monotonic() - started < 3.0
        assert outcome == ["lock file busy"]
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()
        worker.join(5.0)
