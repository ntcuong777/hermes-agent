"""The lock file is safe under concurrent records, refuses a newer schema untouched, and migrates only patch
names (hermes_cli/core_patches_ast.py write_lock_entry, _migrated)."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from hermes_cli import core_patches_ast as cpa

SRC = "def f():\n    return 1\n\n\ndef g():\n    return 2\n"


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    (root / "pkgx.py").write_text(SRC, encoding="utf-8")
    return root


def test_two_threads_recording_different_names_into_one_lock_keep_both_entries(tmp_path):
    root = _root(tmp_path)
    for round_ in range(20):
        plugin_dir = tmp_path / f"plugin{round_}"
        barrier = threading.Barrier(2)
        errors = []

        def record(name, target):
            try:
                barrier.wait(5)
                cpa.write_lock_entry(plugin_dir, name, target, root, provenance={"reason": name})
            except Exception as exc:  # collected, asserted below
                errors.append(exc)
        threads = [threading.Thread(target=record, args=("f", "pkgx:f")),
                   threading.Thread(target=record, args=("g", "pkgx:g"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert errors == []
        assert set(cpa.read_lock(plugin_dir)) == {"f", "g"}


def test_a_lock_with_a_newer_schema_is_refused_and_left_byte_identical(tmp_path):
    root = _root(tmp_path)
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    lock = plugin_dir / cpa.LOCK_FILE
    lock.write_text(json.dumps({"version": 99, "patches": {"f": {"target": "pkgx:f"}}}), encoding="utf-8")
    before = lock.read_bytes()
    with pytest.raises(cpa.CorePatchFileError, match="lock file is a newer schema; update the fork first"):
        cpa.write_lock_entry(plugin_dir, "f", "pkgx:f", root)
    assert lock.read_bytes() == before


def test_a_v1_lock_key_that_is_not_a_patch_name_is_not_migrated_outside_the_plugin_dir(tmp_path):
    root = _root(tmp_path)
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / cpa.LOCK_FILE).write_text(json.dumps({"version": 1, "patches": {
        "../x": {"target": "pkgx:f", "source": SRC}}}), encoding="utf-8")
    cpa.write_lock_entry(plugin_dir, "g", "pkgx:g", root)
    patches = cpa.read_lock(plugin_dir)
    assert "snapshot" not in patches["../x"]  # skipped, not migrated
    assert not (plugin_dir / "x.py.txt").exists() and not (tmp_path / "x.py.txt").exists()
    assert sorted(p.name for p in plugin_dir.rglob("*.txt")) == ["g.py.txt"]
