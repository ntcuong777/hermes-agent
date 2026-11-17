"""`hermes plugins patches`, the doctor row and the catalog admission rule."""

from __future__ import annotations

import argparse
import json
import shutil

import pytest

from hermes_cli import core_patches as cp
from hermes_cli import core_patches_cmd as cmd
from hermes_cli.core_patches_ast import write_lock_entry
from tests.core_patches.conftest import FIXTURES, GREET_SHA


def _args(*argv):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="plugins_action")
    cmd.add_patches_parser(sub)
    return parser.parse_args(["patches", *argv])


def _up(original):
    def greet(name):
        return original(name).upper()
    return greet


def _stage(target, *, sha=GREET_SHA, plugin_dir=None, granted=True):
    spec = cp.make_spec(plugin_id="demo", name="up", target=f"{target.name}:greet", kind="wrap", fn=_up,
                        expect_ast_sha=sha, plugin_dir=plugin_dir)
    key = cp.stage(spec, "o", granted=granted, declared=True)
    cp.activate(key, "o")
    return key


def test_sha_prints_the_fingerprint(target, capsys):
    cmd.cmd_patches(_args("--sha", f"{target.name}:greet"))
    assert capsys.readouterr().out.strip() == GREET_SHA


def test_a_bad_target_exits_2(capsys):
    with pytest.raises(SystemExit) as exited:
        cmd.cmd_patches(_args("--sha", "nope"))
    assert exited.value.code == 2


def test_the_listing_shows_each_status(target, no_discovery, capsys):
    _stage(target)
    cmd.cmd_patches(_args())
    out = capsys.readouterr().out
    assert "active" in out and f"demo:up" in out and f"{target.name}:greet" in out
    cmd.cmd_patches(_args("--json"))
    (row,) = json.loads(capsys.readouterr().out)
    assert row == {"plugin": "demo", "name": "up", "target": f"{target.name}:greet", "kind": "wrap",
                   "order": 0, "critical": False, "status": "active", "detail": ""}


def test_lock_writes_an_entry(target, tmp_path, capsys):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    cmd.cmd_patches(_args("--lock", str(plugin), "--name", "up", "--target", f"{target.name}:greet"))
    assert capsys.readouterr().out.strip() == f"up: {GREET_SHA}"
    assert json.loads((plugin / "core_patches.lock.json").read_text())["patches"]["up"]["ast_sha"] == GREET_SHA


def test_diff_is_quiet_until_the_target_changes(target, tmp_path, no_discovery, capsys):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    write_lock_entry(plugin, "up", f"{target.name}:greet")
    _stage(target, plugin_dir=str(plugin))
    cmd.cmd_patches(_args("--diff"))
    assert "no change since the lock" in capsys.readouterr().out
    target.path.write_text(target.path.read_text().replace('f"hello {name}"', 'f"hi {name}"', 1))
    with pytest.raises(SystemExit) as exited:
        cmd.cmd_patches(_args("--diff", "up"))
    assert exited.value.code == 1
    assert '+    return f"hi {name}"' in capsys.readouterr().out


def test_the_doctor_row_reports_a_drifted_patch(target, no_discovery, capsys):
    _stage(target, sha="2" * 64)
    finding = cmd._check_core_patches(False)
    assert len(finding.manual_issues) == 1 and "skipped-drift" in finding.manual_issues[0]
    assert "Core Patches" in capsys.readouterr().out


def test_the_doctor_row_is_silent_without_patches(no_discovery, capsys):
    assert cmd._check_core_patches(False).manual_issues == []
    assert capsys.readouterr().out == ""


def test_hermes_plugins_patches_is_wired_into_the_cli():
    from hermes_cli.plugins_cmd import _PLUGIN_ACTIONS
    from hermes_cli.subcommands.plugins import build_plugins_parser

    parser = argparse.ArgumentParser()
    build_plugins_parser(parser.add_subparsers(dest="command"), cmd_plugins=lambda args: None)
    args = parser.parse_args(["plugins", "patches", "--sha", "pkg.mod:fn"])
    assert args.plugins_action == "patches" and args.sha == "pkg.mod:fn"
    assert "patches" in _PLUGIN_ACTIONS


def test_hermes_doctor_runs_the_core_patch_row():
    from hermes_cli import doctor

    assert (None, cmd._check_core_patches) in doctor.DOCTOR_CHECKS


def test_catalog_admission_rejects_core_patches(tmp_path):
    from hermes_cli.plugin_validate import ValidationReport, validate_plugin_dir  # noqa: F401
    from hermes_cli.plugin_validate_core_patches import check_core_patches

    class Report:
        def __init__(self):
            self.checks = []

        def add(self, name, ok, detail=""):
            self.checks.append((name, ok, detail))

    demo = tmp_path / "demo"
    shutil.copytree(FIXTURES / "demo-core-patch", demo)
    declared = Report()
    check_core_patches(declared, {"name": "demo", "core_patches": ["shout"]}, demo)
    assert declared.checks[0][:2] == ("core patches", False)
    calls = Report()
    check_core_patches(calls, {"name": "demo"}, demo)
    assert calls.checks[0][:2] == ("core patches", False) and "__init__.py" in calls.checks[0][2]
    (demo / "__init__.py").write_text("def register(ctx):\n    pass\n")
    clean = Report()
    check_core_patches(clean, {"name": "demo"}, demo)
    assert clean.checks == [("core patches", True, "no core patches")]


def test_catalog_admission_calls_the_rule():
    import inspect

    from hermes_cli import plugin_validate

    assert "check_core_patches(report, manifest, plugin_dir)" in inspect.getsource(plugin_validate.validate_plugin_dir)
