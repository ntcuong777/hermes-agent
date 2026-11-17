"""Pytest helper for plugins that register core patches (fork-owned).

In a plugin's tests::

    cpt = pytest.importorskip("hermes_cli.core_patches_testing")

    def test_core_patch_targets_have_not_drifted():
        cpt.assert_core_patches_current(PLUGIN_DIR, expected=EXPECTED_SHAS)

Fails, with the unified diff of each changed target, when the core's source no longer matches
``<plugin>/core_patches.lock.json``; and when ``expected`` (the plugin's ``expect_ast_sha`` literals,
name -> sha) disagrees with the lock.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

from hermes_cli.core_patches_ast import LOCK_FILE, CorePatchTargetError, core_patch_sha, lock_diff, read_lock


def assert_core_patches_current(
    plugin_dir: Path, expected: Optional[Mapping[str, str]] = None, *, source_root: Optional[Path] = None,
) -> None:
    import pytest

    lock = read_lock(Path(plugin_dir))
    if not lock:
        pytest.fail(f"{Path(plugin_dir) / LOCK_FILE} is missing or has no patches")
    if expected is not None:
        locked = {name: entry.get("ast_sha") for name, entry in lock.items()}
        if dict(expected) != locked:
            pytest.fail(f"expect_ast_sha values {dict(expected)} differ from {LOCK_FILE} {locked}; "
                        "refresh with `hermes plugins patches --lock PLUGIN_DIR --name NAME --target TARGET`")
    failures: list[str] = []
    for name, entry in sorted(lock.items()):
        try:
            current = core_patch_sha(str(entry["target"]), source_root)
        except (CorePatchTargetError, KeyError) as exc:
            failures.append(f"{name}: {exc}")
            continue
        if current != entry.get("ast_sha"):
            failures.append(f"{name}: {entry['target']} drifted (locked {entry.get('ast_sha')}, now {current})\n"
                            + lock_diff(entry, source_root))
    if failures:
        pytest.fail("core patch drift:\n" + "\n".join(failures))
