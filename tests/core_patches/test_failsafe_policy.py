"""The fail-safe policy: nothing in the patch system stops, refuses or hangs the agent unless
`core_patches.refuse_on_critical: true` (default false); a bad registration never fails its plugin."""

from __future__ import annotations

import json
import logging
import threading
import time

import pytest

from hermes_cli import core_patches as cp
from hermes_cli import core_patches_policy as policy
from hermes_cli import core_patches_safe as safe
from tests.core_patches.conftest import GREET_SHA, granted_config

OWNER = "home-a"


def _up(original):
    def greet(name):
        return original(name).upper()
    return greet


def _install_critical_drift(target):
    spec = cp.make_spec(plugin_id="demo", name="must", target=f"{target.name}:greet", kind="wrap", fn=_up,
                        expect_ast_sha="1" * 64, critical=True)
    key = cp.stage(spec, OWNER, granted=True, declared=True)
    cp.activate(key, OWNER)


# ── settings ───────────────────────────────────────────────────────────────────────────────


def test_the_defaults_never_refuse():
    assert policy.parse_settings(None) == policy.Settings(
        refuse_on_critical=False, max_failures=3, apply_timeout_s=5.0, notify=True, events_max_bytes=262144)
    assert policy.parse_settings({"refuse_on_critical": "true"}).refuse_on_critical is False  # exactly true only


def test_valid_settings_apply_and_invalid_ones_keep_their_default():
    got = policy.parse_settings({"refuse_on_critical": True, "max_failures": 5, "apply_timeout_s": 0.5,
                                 "notify": False, "events_max_bytes": 8192})
    assert (got.refuse_on_critical, got.max_failures, got.apply_timeout_s, got.notify, got.events_max_bytes) == (
        True, 5, 0.5, False, 8192)
    bad = policy.parse_settings({"max_failures": 0, "apply_timeout_s": -1, "events_max_bytes": 10, "notify": "no"})
    assert (bad.max_failures, bad.apply_timeout_s, bad.events_max_bytes, bad.notify) == (3, 5.0, 262144, True)
    assert policy.parse_settings({"max_failures": True}).max_failures == 3  # a bool is not a count


def test_unreadable_config_means_the_defaults(monkeypatch):
    import hermes_cli.config as config

    def broken():
        raise RuntimeError("config is unreadable")
    monkeypatch.setattr(config, "load_config_readonly", broken)
    assert policy.load_settings() == policy.Settings()


def test_the_flag_is_read_from_the_config_of_the_home(plugin_homes, monkeypatch, no_discovery):
    make, _ = plugin_homes
    home = make("a", granted_config() + "core_patches:\n  refuse_on_critical: true\n  max_failures: 7\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert policy.load_settings().refuse_on_critical is True and policy.load_settings().max_failures == 7


# ── a critical patch is loud, and refuses only with the flag ───────────────────────────────


def test_a_drifted_critical_patch_starts_normally_with_the_flag_off(target, no_discovery, caplog):
    caplog.set_level(logging.ERROR, logger="hermes_cli.core_patches")
    original = target.mod.greet
    _install_critical_drift(target)
    records = cp.apply_core_patches("test")  # does not raise
    assert [r.status for r in records] == [cp.SKIPPED_DRIFT] and target.mod.greet is original
    assert any("runs WITHOUT it" in r.getMessage() and "demo:must" in r.getMessage() for r in caplog.records)
    events = (policy.state_dir() / "events.jsonl").read_text(encoding="utf-8")
    assert '"event": "critical-skipped"' in events


def test_a_drifted_critical_patch_refuses_with_the_flag_on(target, no_discovery, refusing):
    _install_critical_drift(target)
    with pytest.raises(cp.CorePatchRefusal) as refused:
        cp.apply_core_patches("test")
    assert refused.value.code == 78 and "demo:must" in refused.value.message


def test_a_declared_critical_name_with_no_record_starts_with_the_flag_off(plugin_homes, monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger="hermes_cli.core_patches")
    make, log = plugin_homes
    log.FAIL_BEFORE_STAGING = True
    home = make("a", granted_config(), core_patches_yaml="[{name: shout, critical: true}]")
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert cp.apply_core_patches("test") == ()
    assert any("declared critical in plugin.yaml but not registered" in r.getMessage() for r in caplog.records)


def test_the_flag_from_the_real_config_refuses_a_declared_critical_name(plugin_homes, monkeypatch):
    make, log = plugin_homes
    log.FAIL_BEFORE_STAGING = True
    home = make("a", granted_config() + "core_patches:\n  refuse_on_critical: true\n",
                core_patches_yaml="[{name: shout, critical: true}]")
    monkeypatch.setenv("HERMES_HOME", str(home))
    with pytest.raises(cp.CorePatchRefusal):
        cp.apply_core_patches("test")


# ── the call sites ─────────────────────────────────────────────────────────────────────────


def test_a_refusal_raised_while_the_flag_is_off_is_dropped_by_the_apply_site(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger="hermes_cli.core_patches")

    def refuse(surface=""):
        raise cp.CorePatchRefusal("refusing to start: x")
    monkeypatch.setattr(cp, "apply_core_patches", refuse)
    assert safe.apply("cli") is None
    assert any("starting anyway" in r.getMessage() for r in caplog.records)


def test_a_stray_system_exit_in_the_registry_is_swallowed(monkeypatch):
    def leave(surface=""):
        raise SystemExit(3)
    monkeypatch.setattr(cp, "apply_core_patches", leave)
    assert safe.apply("cli") is None


def test_keyboard_interrupt_still_propagates(monkeypatch):
    def interrupt(surface=""):
        raise KeyboardInterrupt
    monkeypatch.setattr(cp, "apply_core_patches", interrupt)
    with pytest.raises(KeyboardInterrupt):
        safe.apply("cli")


def test_the_gateway_site_does_not_publish_startup_failed_with_the_flag_off(monkeypatch):
    import gateway.run

    published = []
    monkeypatch.setattr(gateway.run, "_write_runtime_status_quiet", lambda **fields: published.append(fields))

    def refuse(surface=""):
        raise cp.CorePatchRefusal("refusing to start: x")
    monkeypatch.setattr(cp, "apply_core_patches", refuse)
    safe.gateway_apply()  # returns
    assert published == []


def test_the_cron_site_writes_no_marker_and_ignores_an_old_one_with_the_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    def refuse(surface=""):
        raise cp.CorePatchRefusal("refusing to start: x")
    monkeypatch.setattr(cp, "apply_core_patches", refuse)
    safe.cron_apply()  # returns
    assert not (tmp_path / "cron" / safe.CRON_REFUSAL_FILE).exists()
    marker = tmp_path / "cron" / safe.CRON_REFUSAL_FILE
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"at": time.time(), "until": time.time() + 600, "message": "old"}), encoding="utf-8")
    safe.cron_dispatch_guard(tmp_path)  # a marker from an older version no longer stops a dispatch


# ── the startup budget ─────────────────────────────────────────────────────────────────────


def test_a_hanging_discovery_does_not_block_startup_beyond_the_budget(monkeypatch, caplog):
    import hermes_cli.plugins

    caplog.set_level(logging.WARNING, logger="hermes_cli.core_patches")
    release = threading.Event()
    monkeypatch.setattr(hermes_cli.plugins, "discover_plugins", lambda force=False: release.wait(30))
    monkeypatch.setattr(policy, "load_settings", lambda: policy.Settings(apply_timeout_s=0.2))
    started = time.monotonic()
    try:
        assert cp.apply_core_patches("test") == ()
        assert time.monotonic() - started < 3.0
        assert any("startup budget" in r.getMessage() for r in caplog.records)
    finally:
        release.set()


def test_a_slow_target_import_leaves_the_patch_pending_and_it_installs_when_the_import_finishes(
        plugin_homes, monkeypatch, caplog):
    make, _ = plugin_homes
    home = make("a", granted_config())
    monkeypatch.setenv("HERMES_HOME", str(home))
    import sys

    module = sys.modules["cpdemo_target"]
    release = threading.Event()
    original_probe = cp._probe

    def slow_probe(target, *, may_import):
        release.wait(30)
        return original_probe(target, may_import=may_import)
    monkeypatch.setattr(cp, "_probe", slow_probe)
    monkeypatch.setattr(policy, "load_settings", lambda: policy.Settings(apply_timeout_s=0.2))
    started = time.monotonic()
    try:
        cp.apply_core_patches("test")
        assert time.monotonic() - started < 3.0
        assert module.greet("x") == "hello x"  # not installed yet: the core runs its own behaviour
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and module.greet("x") != "HELLO X":
        time.sleep(0.02)
    assert module.greet("x") == "HELLO X"


# ── a bad registration never fails its plugin ──────────────────────────────────────────────


def test_register_core_patch_with_bad_arguments_returns_none_and_records_the_error():
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest

    ctx = PluginContext(PluginManifest(name="p", key="p", source="user"), PluginManager())
    assert ctx.register_core_patch("shout", target="pkg.mod:fn", kind="wrap", fn=lambda o: o,
                                   expect_ast_sha="not-a-sha") is None
    assert ctx.register_core_patch("Bad Name", target="pkg.mod:fn", kind="wrap", fn=lambda o: o,
                                   expect_ast_sha=GREET_SHA, critical=True) is None
    by_key = {r.spec.key: r for r in cp.records()}
    assert by_key["p:shout"].status == cp.SKIPPED_ERROR and "not registered" in by_key["p:shout"].detail
    assert by_key["p:Bad Name"].critical and by_key["p:Bad Name"].status == cp.SKIPPED_ERROR


def test_a_plugin_with_a_bad_core_patch_still_loads_its_other_registrations(tmp_path, monkeypatch):
    from hermes_cli.plugins import PluginManager

    home = tmp_path / "home"
    plugin = home / "plugins" / "mixed"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: mixed\nversion: 0.1.0\ndescription: t\nauthor: custom\n"
                                        "core_patches: [bad]\n", encoding="utf-8")
    (plugin / "__init__.py").write_text(
        "def register(ctx):\n"
        "    ctx.register_core_patch('bad', target='pkg.mod:fn', kind='nope', fn=lambda o: o, expect_ast_sha='x')\n"
        "    ctx.register_hook('pre_tool_call', lambda **kw: None)\n", encoding="utf-8")
    (home / "config.yaml").write_text("plugins:\n  enabled: [mixed]\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    manager = PluginManager()
    manager.discover_and_load()
    (loaded,) = [p for p in manager.list_plugins() if p["name"] == "mixed"]
    assert loaded["enabled"] and not loaded["error"] and loaded["hooks"] == 1
    assert [r.status for r in cp.records()] == [cp.SKIPPED_ERROR]


# ── the doctor row ─────────────────────────────────────────────────────────────────────────


def test_the_doctor_finding_for_a_critical_patch_says_so(target, no_discovery, capsys):
    from hermes_cli import core_patches_cmd as cmd

    _install_critical_drift(target)
    finding = cmd._check_core_patches(False)
    assert len(finding.manual_issues) == 1 and finding.manual_issues[0].startswith("CRITICAL core patch demo:must")
    out = capsys.readouterr().out
    assert "1 patch(es): skipped-drift=1" in out and "hermes plugins patches" in out


def test_a_broken_report_module_never_breaks_a_load(target, monkeypatch):
    from hermes_cli import core_patches_report as report

    def broken(*args, **kwargs):
        raise RuntimeError("report is broken")
    monkeypatch.setattr(report, "publish", broken)
    spec = cp.make_spec(plugin_id="demo", name="up", target=f"{target.name}:greet", kind="wrap", fn=_up,
                        expect_ast_sha=GREET_SHA)
    key = cp.stage(spec, OWNER, granted=True, declared=True)
    cp.activate(key, OWNER)
    assert target.mod.greet("x") == "HELLO X"
    assert [r.status for r in cp.apply_core_patches("test")] == [cp.ACTIVE]


def test_the_fork_owned_modules_compile_and_the_early_ones_are_stdlib_only():
    import ast
    import sys
    from pathlib import Path

    root = Path(cp.__file__).resolve().parent
    paths = [*root.glob("core_patches*.py"), root / "plugin_validate_core_patches.py"]
    assert len(paths) == 11
    for path in paths:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    for name in ("core_patches_safe", "core_patches_policy", "core_patches_shell", "core_patches_ctx",
                 "core_patches_cmd", "core_patches_ast", "plugin_validate_core_patches"):
        tree = ast.parse((root / f"{name}.py").read_text(encoding="utf-8"))
        top = {a.name.split(".")[0] for n in tree.body if isinstance(n, ast.Import) for a in n.names}
        top |= {n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom) and n.module}
        assert top <= set(sys.stdlib_module_names) | {"__future__"}, (name, top)
