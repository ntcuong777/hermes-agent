"""Provenance: the lock schema v2 (record, snapshot, migration), the lint, the CLI modes and the reports."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli import core_patches as cp
from hermes_cli import core_patches_ast as cpa
from hermes_cli import core_patches_cmd as cmd
from hermes_cli import core_patches_prov as prov
from tests.core_patches.conftest import FIXTURES, GREET_SHA, PROVENANCE

OWNER = "home-a"
SCRIPT = Path(cpa.__file__).resolve()
_MODULES: dict = {}
PATCH_SOURCE = '''import functools


def _shout(original):
    @functools.wraps(original)
    def greet(name):
        return original(name).upper()
    return greet


def french(name):
    return f"bonjour {name}"
'''


def _args(*argv):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="plugins_action")
    cmd.add_patches_parser(sub)
    return parser.parse_args(["patches", *argv])


@pytest.fixture
def plugin(tmp_path):
    """A plugin directory whose module (patch.py) holds a wrap factory and a replacement."""
    directory = tmp_path / "plugins" / "demo"
    directory.mkdir(parents=True)
    (directory / "patch.py").write_text(PATCH_SOURCE, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("cp_demo_patch", directory / "patch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MODULES[str(directory)] = module
    return directory


def _install(target, plugin, *, kind="wrap", sha=GREET_SHA, name="up"):
    module = _MODULES[str(plugin)]
    fn = module._shout if kind == "wrap" else module.french
    spec = cp.make_spec(plugin_id="demo", name=name, target=f"{target.name}:greet", kind=kind, fn=fn,
                        expect_ast_sha=sha, plugin_dir=str(plugin))
    key = cp.stage(spec, OWNER, granted=True, declared=True)
    cp.activate(key, OWNER)
    return next(r for r in cp.records() if r.spec.key == key)


def _root(target):
    return target.path.parent


# ── record ─────────────────────────────────────────────────────────────────────────────────


def test_record_writes_a_complete_provenance_record_and_a_verbatim_snapshot(target, plugin):
    record = _install(target, plugin)
    done = prov.record(record, "greeting must be shouted", source_root=_root(target))
    entry = cpa.read_lock(plugin)["up"]
    assert done["ast_sha"] == entry["ast_sha"] == GREET_SHA and done["stale_expectation"] is False
    assert (entry["plugin"], entry["kind"], entry["reason"]) == ("demo", "wrap", "greeting must be shouted")
    assert entry["target"] == f"{target.name}:greet" and entry["target_file"] == f"{target.name}.py"
    assert entry["patch_fn"] == "patch.py:_shout" and entry["copy_of"] is None
    assert entry["authored_at"].endswith("Z") and entry["recorded_at"].endswith("Z") and entry["fork_head"]
    assert set(entry["upstream_ref"]) == {"commit", "release", "date"}
    snapshot = (plugin / entry["snapshot"]).read_text(encoding="utf-8")
    assert snapshot == cpa.target_source(f"{target.name}:greet", _root(target)).source
    assert entry["snapshot_sha256"] == cpa._sha256(snapshot)
    assert cpa.provenance_problems("demo/up", entry, plugin) == []


def test_record_for_a_replace_patch_names_the_copy(target, plugin):
    record = _install(target, plugin, kind="replace")
    prov.record(record, "copy of greet", source_root=_root(target))
    entry = cpa.read_lock(plugin)["up"]
    assert entry["kind"] == "replace" and entry["copy_of"] == entry["patch_fn"] == "patch.py:french"


def test_record_again_keeps_authored_at_and_refreshes_the_snapshot_after_a_change(target, plugin):
    record = _install(target, plugin)
    prov.record(record, "first", source_root=_root(target))
    first = cpa.read_lock(plugin)["up"]
    target.path.write_text(target.path.read_text().replace('f"hello {name}"', 'f"hi {name}"', 1), encoding="utf-8")
    done = prov.record(record, "second", source_root=_root(target))
    second = cpa.read_lock(plugin)["up"]
    assert second["authored_at"] == first["authored_at"] and second["reason"] == "second"
    assert second["ast_sha"] != GREET_SHA and done["stale_expectation"] is True
    assert "hi {name}" in (plugin / second["snapshot"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("reason", ["", "   ", "two\nlines", "x" * 241])
def test_record_needs_a_one_line_reason(target, plugin, reason):
    with pytest.raises(ValueError, match="--reason"):
        prov.record(_install(target, plugin), reason, source_root=_root(target))


def test_the_record_command_prints_the_next_step_when_the_expectation_is_stale(target, plugin, no_discovery, capsys):
    _install(target, plugin, sha="5" * 64)
    cmd.cmd_patches(_args("--record", "up", "--reason", "demo"))
    out = capsys.readouterr().out
    assert "up: recorded " in out and f"next: set expect_ast_sha to {GREET_SHA}" in out
    with pytest.raises(SystemExit) as exited:
        cmd.cmd_patches(_args("--record", "up"))
    assert exited.value.code == 2
    with pytest.raises(SystemExit) as exited:
        cmd.cmd_patches(_args("--record", "nope", "--reason", "x"))
    assert exited.value.code == 2 and "no core patch named" in capsys.readouterr().err


# ── diff, brief, report ────────────────────────────────────────────────────────────────────


def _drift(target):
    target.path.write_text(target.path.read_text().replace('f"hello {name}"', 'f"hi {name}"', 1), encoding="utf-8")


def test_upstream_diff_shows_what_upstream_changed_and_what_the_copy_changed(target, plugin, no_discovery, capsys):
    record = _install(target, plugin, kind="replace")
    prov.record(record, "copy of greet", source_root=_root(target))
    _drift(target)
    cmd.cmd_patches(_args("--upstream-diff", "up"))
    out = capsys.readouterr().out
    assert "recorded upstream snapshot -> current core" in out and '+    return f"hi {name}"' in out
    assert "recorded upstream snapshot -> the plugin's copy (patch.py:french)" in out and "bonjour" in out


def test_the_agent_brief_is_self_contained(target, plugin, no_discovery, capsys):
    record = _install(target, plugin, kind="replace")
    prov.record(record, "greeting must be French", source_root=_root(target))
    _drift(target)
    cmd.cmd_patches(_args("--agent-brief", "up"))
    brief = capsys.readouterr().out
    for needle in ("# Core patch brief: demo:up", "greeting must be French", "recorded against upstream commit",
                   "## Old upstream source (recorded snapshot)", 'return f"hello {name}"',
                   "## Current core source", 'return f"hi {name}"', "## Diff old -> current",
                   "copied replacement (`patch.py:french`)", "def french(name):",
                   "hermes plugins patches --record up --reason", "hermes plugins patches --sha",
                   "--write-pins", "python -m pytest tests -q -p no:cacheprovider", "git commit --no-verify",
                   "never open an upstream pull request", "run_tests.sh -j 3 <explicit files>",
                   "## Rules of this fork"):
        assert needle in brief, needle


def test_the_report_has_a_table_and_the_diff_of_a_drifted_patch(target, plugin, no_discovery, capsys):
    record = _install(target, plugin)
    prov.record(record, "greeting must be shouted", source_root=_root(target))
    cp.release(record.spec.key, OWNER)
    _drift(target)
    _install(target, plugin, sha="6" * 64)
    cmd.cmd_patches(_args("--report"))
    out = capsys.readouterr().out
    assert "| patch | target | status |" in out and "| `demo:up` |" in out and "greeting must be shouted" in out
    assert "### demo:up (skipped-drift)" in out and '+    return f"hi {name}"' in out
    assert "--agent-brief up" in out


def test_report_exits_zero_even_with_drift_unless_strict(target, plugin, no_discovery):
    _install(target, plugin, sha="6" * 64)
    cmd.cmd_patches(_args("--report"))
    with pytest.raises(SystemExit) as exited:
        cmd.cmd_patches(_args("--report", "--strict"))
    assert exited.value.code == 1


# ── lint ───────────────────────────────────────────────────────────────────────────────────


def _complete(tmp_path, target):
    plugin_dir = tmp_path / "p"
    plugin_dir.mkdir()
    cpa.write_lock_entry(plugin_dir, "up", f"{target.name}:greet", _root(target), provenance=PROVENANCE)
    return plugin_dir, cpa.read_lock(plugin_dir)["up"]


def test_a_complete_entry_passes_the_lint(tmp_path, target):
    plugin_dir, entry = _complete(tmp_path, target)
    assert cpa.provenance_problems("p/up", entry, plugin_dir) == []


@pytest.mark.parametrize("field, expect", [
    ("reason", "no reason"), ("authored_at", "no authored_at"), ("fork_head", "no fork_head"),
    ("upstream_ref", "no upstream_ref.commit"), ("snapshot", "no snapshot"),
])
def test_each_missing_field_is_reported(tmp_path, target, field, expect):
    plugin_dir, entry = _complete(tmp_path, target)
    entry.pop(field)
    (line,) = [p for p in cpa.provenance_problems("p/up", entry, plugin_dir) if expect in p]
    assert line.startswith("PROVENANCE p/up:") and "--record up" in line


def test_a_multiline_reason_and_a_tampered_snapshot_are_reported(tmp_path, target):
    plugin_dir, entry = _complete(tmp_path, target)
    assert any("one line" in p for p in cpa.provenance_problems("p/up", {**entry, "reason": "a\nb"}, plugin_dir))
    (plugin_dir / entry["snapshot"]).write_text("def greet():\n    pass\n", encoding="utf-8")
    assert any("does not match snapshot_sha256" in p for p in cpa.provenance_problems("p/up", entry, plugin_dir))
    (plugin_dir / entry["snapshot"]).unlink()
    assert any("is missing" in p for p in cpa.provenance_problems("p/up", entry, plugin_dir))


def test_a_snapshot_path_outside_the_snapshot_directory_is_never_read(tmp_path, target):
    plugin_dir, entry = _complete(tmp_path, target)
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    assert cpa.snapshot_text(plugin_dir, {**entry, "snapshot": "../secret.txt"}) is None


def test_verify_locks_and_the_pins_report_a_patch_without_provenance(tmp_path, target, capsys):
    plugins = tmp_path / "plugins"
    (plugins / "bare").mkdir(parents=True)
    cpa.write_lock_entry(plugins / "bare", "up", f"{target.name}:greet", _root(target))  # no provenance
    problems = cpa.verify_locks(plugins, _root(target))
    assert any("no reason" in p for p in problems) and any("no upstream_ref.commit" in p for p in problems)
    pins = tmp_path / "pins.json"
    cpa.write_pins(plugins, pins)
    assert cpa.main(["--verify-pins", str(pins), "--source-root", str(_root(target))]) == 1
    assert "PROVENANCE bare/up: the pin has no reason" in capsys.readouterr().err


def test_the_test_helper_fails_a_plugin_without_provenance(tmp_path, target):
    from hermes_cli.core_patches_testing import assert_core_patches_current

    bare = tmp_path / "bare"
    bare.mkdir()
    cpa.write_lock_entry(bare, "up", f"{target.name}:greet", _root(target))
    with pytest.raises(pytest.fail.Exception, match="PROVENANCE"):
        assert_core_patches_current(bare, source_root=_root(target))
    assert_core_patches_current(bare, source_root=_root(target), provenance=False)
    complete, _ = _complete(tmp_path, target)
    assert_core_patches_current(complete, source_root=_root(target))


# ── schema migration ───────────────────────────────────────────────────────────────────────


def test_a_v1_lock_is_still_read_and_migrates_to_v2(tmp_path):
    legacy = tmp_path / "legacy"
    shutil.copytree(FIXTURES / "legacy-lock", legacy)
    old = json.loads((legacy / cpa.LOCK_FILE).read_text())["patches"]["shout"]
    assert cpa.lock_version(legacy) == 1 and cpa.snapshot_text(legacy, old) == old["source"]
    assert cpa.lock_diff(old, FIXTURES, legacy) == ""  # a v1 entry still diffs through its inline source
    assert cpa.migrate_lock(legacy) == 1
    new = cpa.read_lock(legacy)["shout"]
    assert cpa.lock_version(legacy) == 2 and "source" not in new and new["migrated_from"] == 1
    assert (legacy / new["snapshot"]).read_text(encoding="utf-8") == old["source"]
    assert new["snapshot_sha256"] == cpa._sha256(old["source"]) and new["ast_sha"] == old["ast_sha"]
    assert any("no reason" in p for p in cpa.provenance_problems("legacy/shout", new, legacy))  # the lint asks for it
    assert cpa.migrate_lock(legacy) == 0  # idempotent


def test_recording_one_patch_migrates_the_other_v1_entries_of_the_lock(tmp_path, target):
    plugin_dir = tmp_path / "mixed"
    shutil.copytree(FIXTURES / "legacy-lock", plugin_dir)
    cpa.write_lock_entry(plugin_dir, "up", f"{target.name}:greet", _root(target), provenance=PROVENANCE)
    patches = cpa.read_lock(plugin_dir)
    assert set(patches) == {"shout", "up"} and cpa.lock_version(plugin_dir) == 2
    assert all("snapshot" in entry and "source" not in entry for entry in patches.values())


# ── markdown reports ───────────────────────────────────────────────────────────────────────


def test_report_locks_shows_the_table_and_the_diff_of_a_drifted_patch(tmp_path, target):
    plugins = tmp_path / "plugins"
    (plugins / "demo").mkdir(parents=True)
    cpa.write_lock_entry(plugins / "demo", "up", f"{target.name}:greet", _root(target), provenance=PROVENANCE)
    clean = cpa.report_locks(plugins, _root(target))
    assert "0 of 1 need attention" in clean and "DRIFT" not in clean
    _drift(target)
    drifted = cpa.report_locks(plugins, _root(target))
    assert "1 of 1 need attention" in drifted and "| `demo/up` |" in drifted and "DRIFT" in drifted
    assert '+    return f"hi {name}"' in drifted and "e96221fa73d2" in drifted


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "-c",
                           "commit.gpgsign=false", *args], capture_output=True, text=True, check=True).stdout.strip()


def test_report_pins_lists_the_commits_that_touched_the_file_since_the_recorded_one(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "mod.py").write_text("def greet(name):\n    return name\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "recorded state")
    recorded = _git(root, "rev-parse", "HEAD")
    sha = cpa.core_patch_sha("mod:greet", root)
    (root / "mod.py").write_text("def greet(name):\n    return name.title()\n", encoding="utf-8")
    _git(root, "commit", "-q", "-am", "upstream: title-case greet")
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps({"version": 2, "pins": {"demo/up": {
        "target": "mod:greet", "ast_sha": sha, "plugin": "demo", "kind": "wrap", "target_file": "mod.py",
        "upstream_commit": recorded, "upstream_release": "0.23.0", "reason": "demo"}}}), encoding="utf-8")
    text = cpa.report_pins(pins, root)
    assert "1 of 1 need attention" in text and "upstream: title-case greet" in text and "recorded state" not in text


def test_the_script_report_and_migrate_modes_exit_zero(tmp_path, target):
    plugins = tmp_path / "plugins"
    (plugins / "demo").mkdir(parents=True)
    cpa.write_lock_entry(plugins / "demo", "up", f"{target.name}:greet", _root(target), provenance=PROVENANCE)
    for flag, value in (("--report-locks", plugins),):
        out = subprocess.run([sys.executable, "-I", str(SCRIPT), flag, str(value), "--source-root", str(_root(target))],
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0 and "## Core patches" in out.stdout
    missing = subprocess.run([sys.executable, "-I", str(SCRIPT), "--report-pins", str(tmp_path / "none.json")],
                             capture_output=True, text=True, timeout=60)
    assert missing.returncode == 0 and "Cannot read the pins" in missing.stdout
    legacy = tmp_path / "legacy"
    shutil.copytree(FIXTURES / "legacy-lock", legacy)
    moved = subprocess.run([sys.executable, "-I", str(SCRIPT), "--migrate-lock", str(legacy)],
                           capture_output=True, text=True, timeout=60)
    assert moved.returncode == 0 and "migrated 1" in moved.stdout
