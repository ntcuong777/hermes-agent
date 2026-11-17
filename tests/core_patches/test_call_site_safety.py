"""A broken fork module never breaks an upstream command; refusals mirror the existing ones
(review findings 5 and 6)."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time

import pytest

from hermes_cli import core_patches as cp
from hermes_cli import core_patches_safe as safe


@pytest.fixture
def broken_cmd_module(monkeypatch):
    """``import hermes_cli.core_patches_cmd`` raises (an upstream rename broke it)."""
    import hermes_cli

    monkeypatch.delattr(hermes_cli, "core_patches_cmd", raising=False)
    monkeypatch.setitem(sys.modules, "hermes_cli.core_patches_cmd", None)


def test_the_plugins_parser_builds_when_the_registry_module_is_broken(broken_cmd_module):
    from hermes_cli.subcommands.plugins import build_plugins_parser

    parser = argparse.ArgumentParser()
    build_plugins_parser(parser.add_subparsers(dest="command"), cmd_plugins=lambda args: None)
    assert parser.parse_args(["plugins", "list"]).command == "plugins"


def test_the_doctor_row_is_an_empty_finding_when_the_registry_module_is_broken(broken_cmd_module):
    from hermes_cli import doctor

    (check,) = [c for _, c in doctor.DOCTOR_CHECKS if c is safe.doctor_check]
    assert check(False).manual_issues == []


def test_hermes_plugins_patches_is_a_clear_error_when_the_registry_module_is_broken(broken_cmd_module, capsys):
    with pytest.raises(SystemExit) as exited:
        safe.cmd_patches(argparse.Namespace())
    assert exited.value.code == 1 and "core-patch registry is unavailable" in capsys.readouterr().err


def test_the_cmd_module_imports_without_the_doctor_report_names(monkeypatch):
    import hermes_cli.doctor_report as doctor_report

    for name in ("Finding", "_section", "check_ok", "check_warn", "doctor_check"):
        monkeypatch.delattr(doctor_report, name)
    monkeypatch.delitem(sys.modules, "hermes_cli.core_patches_cmd", raising=False)
    module = importlib.import_module("hermes_cli.core_patches_cmd")
    assert callable(module.add_patches_parser)
    monkeypatch.delitem(sys.modules, "hermes_cli.core_patches_cmd", raising=False)


def test_an_apply_site_survives_a_broken_registry(monkeypatch):
    def broken(surface=""):
        raise AttributeError("upstream renamed something")
    monkeypatch.setattr(cp, "apply_core_patches", broken)
    assert safe.apply("cli") is None


def test_an_apply_site_still_refuses(monkeypatch):
    def refuse(surface=""):
        raise cp.CorePatchRefusal("refusing to start: x")
    monkeypatch.setattr(cp, "apply_core_patches", refuse)
    with pytest.raises(cp.CorePatchRefusal):
        safe.apply("cli")


# ── finding 6: the gateway publishes startup_failed; a cron worker refuses once ─────────────


def test_the_gateway_refusal_publishes_startup_failed_and_exits_78(monkeypatch):
    import gateway.run
    import hermes_cli.plugins
    import hermes_cli.plugins_cmd_remove
    from gateway.run_startup import GatewayStartupMixin

    published = []
    monkeypatch.setattr(gateway.run, "_write_runtime_status_quiet", lambda **fields: published.append(fields))
    monkeypatch.setattr(hermes_cli.plugins, "discover_plugins", lambda force=False: None)
    monkeypatch.setattr(hermes_cli.plugins_cmd_remove, "sweep_pending_plugin_deletes", lambda: None)

    def refuse(surface=""):
        raise cp.CorePatchRefusal("refusing to start: critical core patch demo:x is skipped-drift")
    monkeypatch.setattr(cp, "apply_core_patches", refuse)
    with pytest.raises(SystemExit) as exited:
        GatewayStartupMixin._start_register_plugins_relay_hooks()
    assert exited.value.code == 78
    assert published == [{"gateway_state": "startup_failed",
                          "exit_reason": "core_patch_refusal: refusing to start: critical core patch demo:x is "
                                         "skipped-drift"}]


def test_a_cron_worker_refusal_is_recorded_and_the_next_spawn_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    def refuse(surface=""):
        raise cp.CorePatchRefusal("refusing to start: demo:x")
    monkeypatch.setattr(cp, "apply_core_patches", refuse)
    with pytest.raises(SystemExit) as exited:
        safe.cron_apply()
    assert exited.value.code == 78
    marker = json.loads((tmp_path / "cron" / safe.CRON_REFUSAL_FILE).read_text(encoding="utf-8"))
    assert marker["message"] == "refusing to start: demo:x"
    with pytest.raises(RuntimeError, match="not spawning another"):
        safe.cron_dispatch_guard(tmp_path)
    monkeypatch.setattr(time, "time", lambda: marker["until"] + 1)
    safe.cron_dispatch_guard(tmp_path)  # the cooldown is over: dispatch again


def test_the_cron_guard_is_silent_without_a_refusal(tmp_path):
    safe.cron_dispatch_guard(tmp_path)
