"""migrate_lock refuses a lock with a newer schema, untouched, and still migrates a v1 lock
(hermes_cli/core_patches_ast.py migrate_lock)."""

from __future__ import annotations

import json

import pytest

from hermes_cli import core_patches_ast as cpa

SRC = "def f():\n    return 1\n"


def test_migrate_lock_refuses_a_newer_schema_and_leaves_the_file_byte_identical(tmp_path):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    lock = plugin_dir / cpa.LOCK_FILE
    lock.write_text(json.dumps({"version": 99, "patches": {"f": {"target": "pkgx:f", "source": SRC}}}),
                    encoding="utf-8")
    before = lock.read_bytes()
    with pytest.raises(cpa.CorePatchFileError, match="lock file is a newer schema; update the fork first"):
        cpa.migrate_lock(plugin_dir)
    assert lock.read_bytes() == before
    assert not (plugin_dir / cpa.snapshot_rel("f")).exists()


def test_migrate_lock_still_migrates_a_v1_lock_to_the_current_schema(tmp_path):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / cpa.LOCK_FILE).write_text(json.dumps({"version": 1, "patches": {
        "f": {"target": "pkgx:f", "source": SRC}}}), encoding="utf-8")
    assert cpa.migrate_lock(plugin_dir) == 1
    assert cpa.lock_version(plugin_dir) == cpa.LOCK_VERSION
    entry = cpa.read_lock(plugin_dir)["f"]
    assert "source" not in entry and entry["snapshot"] == cpa.snapshot_rel("f")
    assert (plugin_dir / entry["snapshot"]).read_text(encoding="utf-8") == SRC
