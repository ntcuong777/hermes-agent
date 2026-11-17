"""AST fingerprints, lock files and pins (hermes_cli/core_patches_ast.py, stdlib only)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli import core_patches_ast as cpa
from tests.core_patches.conftest import (
    AGREET_SHA, BOX_MAKE_SHA, BOX_SIZE_SHA, CHANGED_GREET_SHA, FIXTURES, GREET_SHA,
)

SCRIPT = Path(cpa.__file__).resolve()


@pytest.mark.parametrize("target, sha", [
    ("cpdemo_target:greet", GREET_SHA), ("cpdemo_target:agreet", AGREET_SHA),
    ("cpdemo_target:Box.size", BOX_SIZE_SHA), ("cpdemo_target:Box.make", BOX_MAKE_SHA),
])
def test_fixture_fingerprints_are_the_golden_values(target, sha):
    assert cpa.core_patch_sha(target, FIXTURES) == sha


def test_comments_whitespace_and_docstring_do_not_drift():
    assert cpa.core_patch_sha("cpdemo_variant:greet", FIXTURES) == GREET_SHA


def test_a_code_change_drifts():
    assert cpa.core_patch_sha("cpdemo_changed:greet", FIXTURES) == CHANGED_GREET_SHA


@pytest.mark.parametrize("target", ["no-colon", "pkg.mod:", "pkg.mod:a.b.c", "cpdemo_missing:greet",
                                    "cpdemo_target:nope", "cpdemo_target:Sub.size"])
def test_bad_or_missing_targets_raise(target):
    with pytest.raises(cpa.CorePatchTargetError):
        cpa.core_patch_sha(target, FIXTURES)


def test_the_source_text_includes_decorators():
    assert cpa.target_source("cpdemo_target:Box.make", FIXTURES).source.lstrip().startswith("@staticmethod")


def _copy_fixtures(tmp_path):
    root = tmp_path / "src"
    shutil.copytree(FIXTURES, root)
    plugins = tmp_path / "plugins"
    (plugins / "demo").mkdir(parents=True)
    return root, plugins / "demo"


def test_lock_roundtrip_diff_and_verify(tmp_path):
    root, plugin = _copy_fixtures(tmp_path)
    assert cpa.write_lock_entry(plugin, "shout", "cpdemo_target:greet", root) == GREET_SHA
    entry = cpa.read_lock(plugin)["shout"]
    assert entry["target"] == "cpdemo_target:greet" and entry["ast_sha"] == GREET_SHA
    assert cpa.lock_diff(entry, root) == ""
    assert cpa.verify_locks(plugin.parent, root) == []
    source = root / "cpdemo_target.py"
    source.write_text(source.read_text(encoding="utf-8").replace('f"hello {name}"', 'f"hi {name}"', 1),
                      encoding="utf-8")
    diff = cpa.lock_diff(entry, root)
    assert '-    return f"hello {name}"' in diff and '+    return f"hi {name}"' in diff
    (problem,) = cpa.verify_locks(plugin.parent, root)
    assert problem.startswith("DRIFT demo/shout cpdemo_target:greet")


def test_pins_roundtrip(tmp_path):
    root, plugin = _copy_fixtures(tmp_path)
    cpa.write_lock_entry(plugin, "shout", "cpdemo_target:greet", root)
    pins = tmp_path / "pins.json"
    assert cpa.write_pins(plugin.parent, pins) == 1
    assert json.loads(pins.read_text())["pins"] == {"demo/shout": {"target": "cpdemo_target:greet", "ast_sha": GREET_SHA}}
    assert cpa.verify_pins(pins, root) == []
    (root / "cpdemo_target.py").write_text("def greet(name):\n    return name\n", encoding="utf-8")
    (problem,) = cpa.verify_pins(pins, root)
    assert problem.startswith("DRIFT demo/shout")


def test_it_runs_as_a_standalone_script(tmp_path):
    out = subprocess.run([sys.executable, "-I", str(SCRIPT), "--sha", "cpdemo_target:greet", "--source-root",
                          str(FIXTURES)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == GREET_SHA
    root, plugin = _copy_fixtures(tmp_path)
    cpa.write_lock_entry(plugin, "shout", "cpdemo_target:greet", root)
    ok = subprocess.run([sys.executable, "-I", str(SCRIPT), "--verify-locks", str(plugin.parent), "--source-root",
                         str(root)], capture_output=True, text=True, timeout=60)
    assert ok.returncode == 0 and "no drift" in ok.stdout
    (root / "cpdemo_target.py").write_text("def greet(name):\n    return name\n", encoding="utf-8")
    bad = subprocess.run([sys.executable, "-I", str(SCRIPT), "--verify-locks", str(plugin.parent), "--source-root",
                          str(root)], capture_output=True, text=True, timeout=60)
    assert bad.returncode == 1 and "DRIFT demo/shout" in bad.stderr


def test_the_module_imports_only_the_standard_library():
    import ast as _ast

    tree = _ast.parse(SCRIPT.read_text(encoding="utf-8"))
    top = {alias.name.split(".")[0] for node in tree.body if isinstance(node, _ast.Import) for alias in node.names}
    top |= {node.module.split(".")[0] for node in tree.body if isinstance(node, _ast.ImportFrom) and node.module}
    assert top <= set(sys.stdlib_module_names) | {"__future__"}
