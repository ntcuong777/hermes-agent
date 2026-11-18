"""Spec 09: `on_drift` (apply by default, skip = the old behaviour) for the core-patch registry."""

from __future__ import annotations

import json
import logging

import pytest

from hermes_cli import core_patches as cp
from hermes_cli import core_patches_cmd as cmd
from hermes_cli import core_patches_policy as policy
from tests.core_patches.conftest import GREET_SHA

OWNER = "home-a"
DRIFTED_SHA = "0" * 64


def up(original):
    def greet(name):
        return original(name).upper()
    return greet


def _install(target, *, sha=DRIFTED_SHA, attr="greet", kind="wrap", fn=up, on_drift=None, critical=False, name="up"):
    spec = cp.make_spec(plugin_id="demo", name=name, target=f"{target.name}:{attr}", kind=kind, fn=fn,
                        expect_ast_sha=sha, on_drift=on_drift, critical=critical)
    key = cp.stage(spec, OWNER, granted=True, declared=True)
    cp.activate(key, OWNER)
    return {r.spec.key: r for r in cp.records()}[key]


def test_the_default_is_apply_and_the_setting_parses_strictly():
    assert policy.parse_settings(None).on_drift == "apply"
    assert policy.parse_settings({"on_drift": "skip"}).on_drift == "skip"
    assert policy.parse_settings({"on_drift": "sometimes"}).on_drift == "apply"
    with pytest.raises(ValueError):
        cp.make_spec(plugin_id="d", name="x", target="a.b:c", kind="wrap", fn=up, expect_ast_sha=DRIFTED_SHA,
                     on_drift="maybe")


def test_apply_installs_a_drifted_patch_as_active_with_the_drift_in_its_detail(target, caplog):
    caplog.set_level(logging.WARNING, logger="hermes_cli.core_patches")
    original = target.mod.greet
    record = _install(target)
    assert record.status == cp.ACTIVE and record.drifted
    assert "expected 000000000000, now 8ac9a553d27b" in record.detail and "on_drift apply" in record.detail
    assert target.mod.greet is not original and target.mod.greet("x") == "HELLO X"


def test_apply_logs_one_warning_per_drifted_patch_with_everything_a_later_agent_needs(target, no_discovery, caplog):
    caplog.set_level(logging.INFO, logger="hermes_cli.core_patches")
    _install(target)
    cp.apply_core_patches("test")
    cp.apply_core_patches("test")
    (line,) = [r.getMessage() for r in caplog.records if "demo:up drifted but applied" in r.getMessage()]
    assert "plugin=demo" in line and f"target={target.name}:greet" in line
    assert "expected-sha=000000000000" in line and "current-sha=8ac9a553d27b" in line
    assert "recorded-upstream=" in line and "--agent-brief up" in line and "--upstream-diff up" in line
    assert "drifted=1" in [r.getMessage() for r in caplog.records if "core patches (test)" in r.getMessage()][0]


def test_status_events_listing_and_strict_report_the_drift(target, no_discovery, capsys):
    _install(target)
    cp.apply_core_patches("test")
    status = json.loads((policy.state_dir() / "status.json").read_text())
    assert status["patches"]["demo:up"]["drifted"] is True and status["patches"]["demo:up"]["status"] == "active"
    events = [json.loads(x) for x in (policy.state_dir() / "events.jsonl").read_text().splitlines()]
    assert [(e["event"], e["key"]) for e in events] == [("drift-first-seen", "demo:up")]
    cmd.cmd_patches(__import__("argparse").Namespace(**{k: None for k in (
        "sha", "diff", "lock", "record", "reason", "upstream_diff", "agent_brief", "migrate_lock", "name", "target",
        "verify_pins", "plugins_root")}, patches_json=False, report=False, strict=False))
    assert "DRIFTED" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exited:  # --strict still exits 1 on drift, as before
        cmd.cmd_patches(__import__("argparse").Namespace(**{k: None for k in (
            "sha", "diff", "lock", "record", "reason", "upstream_diff", "agent_brief", "migrate_lock", "name", "target",
            "verify_pins", "plugins_root")}, patches_json=False, report=False, strict=True))
    assert exited.value.code == 1


def test_a_drifted_critical_patch_that_runs_does_not_refuse_and_logs_at_error(target, no_discovery, refusing, caplog):
    caplog.set_level(logging.WARNING, logger="hermes_cli.core_patches")
    _install(target, critical=True, name="must")
    assert [r.status for r in cp.apply_core_patches("test")] == [cp.ACTIVE]  # installed: nothing to refuse
    assert [r.levelno for r in caplog.records if "demo:must drifted but applied" in r.getMessage()] == [logging.ERROR]


def test_the_doctor_lists_a_drifted_patch_as_a_problem(target, no_discovery, capsys):
    _install(target)
    finding = cmd._check_core_patches(False)
    assert len(finding.manual_issues) == 1 and "drifted" in finding.manual_issues[0]
    assert "runs the plugin's patch anyway" in finding.manual_issues[0]


def test_a_drifted_patch_that_raises_is_still_answered_by_the_original(target):
    def buggy(original):
        def greet(name):
            raise RuntimeError("copy broke on the new upstream")
        return greet
    _install(target, fn=buggy)
    assert target.mod.greet("x") == "hello x"  # the existing safety shell: the original answers, nothing fails


def _guard(key):
    return cp._LINKS[{r.spec.key: r for r in cp.records()}[key].spec.seq].guard


def test_a_drifted_replace_that_raises_a_non_bug_class_error_is_answered_by_the_original_and_counted(target):
    def broken(name):
        raise ValueError("copy rejects the new argument shape")
    _install(target, kind="replace", fn=broken, name="swap")
    assert target.mod.greet("x") == "hello x"  # ValueError is not bug-class, but the drifted copy is answered
    assert (_guard("demo:swap").total, _guard("demo:swap").consecutive) == (1, 1)


def test_a_non_drifted_replace_that_raises_a_non_bug_class_error_still_propagates(target):
    def broken(name):
        raise ValueError("copy rejects the new argument shape")
    record = _install(target, kind="replace", fn=broken, sha=GREET_SHA, name="swap")
    assert record.status == cp.ACTIVE and not record.drifted
    with pytest.raises(ValueError):  # unchanged: a clean replace's legitimate errors are not answered
        target.mod.greet("x")


def test_skip_in_the_registration_or_the_config_reproduces_the_old_behaviour(target, monkeypatch):
    original = target.mod.greet
    record = _install(target, on_drift="skip")
    assert record.status == cp.SKIPPED_DRIFT and not record.drifted and target.mod.greet is original
    cp.release(record.spec.key, OWNER)
    monkeypatch.setattr(policy, "load_settings", lambda: policy.Settings(on_drift="skip"))
    assert _install(target).status == cp.SKIPPED_DRIFT and target.mod.greet is original
    cp.release("demo:up", OWNER)
    assert _install(target, on_drift="apply").status == cp.ACTIVE  # the registration beats core_patches.on_drift


def test_hard_cases_stay_skipped_under_apply(target):
    assert _install(target, attr="greet", sha=GREET_SHA).drifted is False  # no drift: unchanged behaviour
    gone = _install(target, attr="no_such_function", name="gone")
    assert gone.status == cp.SKIPPED_ERROR and "target unresolvable" in gone.detail


def test_a_file_rewritten_after_import_is_still_skipped_under_apply(target):
    assert _install(target, sha=GREET_SHA).status == cp.ACTIVE
    target.path.write_text(target.path.read_text(encoding="utf-8") + "\n# updated\n", encoding="utf-8")
    record = _install(target, sha=GREET_SHA)  # a hot reload sees the rewritten file
    assert record.status == cp.SKIPPED_DRIFT and "source changed since import" in record.detail
