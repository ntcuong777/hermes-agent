"""Registry lifecycle through the real PluginManager: failed loads, grants across homes, forced reload,
lazy target resolution, concurrent apply (review findings 1, 3, 4, 7)."""

from __future__ import annotations

import importlib
import logging
import sys
import threading

import pytest

from hermes_cli import core_patches as cp
from tests.core_patches.conftest import GREET_SHA, granted_config


def _manager(home, monkeypatch=None):
    from hermes_cli.plugins import PluginManager

    if monkeypatch is not None:
        monkeypatch.setenv("HERMES_HOME", str(home))
    return PluginManager(scope_key=str(home))


def _state():
    return [(r.spec.key, r.status) for r in cp.records()]


# ── finding 1: a critical patch whose plugin fails to load still refuses ────────────────────


def test_a_critical_patch_whose_register_raises_after_staging_leaves_a_tombstone(plugin_homes, monkeypatch,
                                                                                 no_discovery, refusing):
    make, log = plugin_homes
    log.CRITICAL, log.FAIL_AFTER_STAGING = True, True
    home = make("a", granted_config())
    module = sys.modules["cpdemo_target"]
    original = module.greet
    _manager(home, monkeypatch).discover_and_load()
    assert module.greet is original
    ((key, status),) = _state()
    assert (key, status) == ("shout:shout", cp.SKIPPED_ERROR)
    assert "plugin failed to load" in cp.records()[0].detail
    with pytest.raises(cp.CorePatchRefusal) as refused:
        cp.apply_core_patches("test")
    assert refused.value.code == 78 and "shout:shout" in refused.value.message


def test_a_non_critical_patch_whose_load_fails_leaves_nothing(plugin_homes, monkeypatch, no_discovery):
    make, log = plugin_homes
    log.FAIL_AFTER_STAGING = True
    _manager(make("a", granted_config()), monkeypatch).discover_and_load()
    assert _state() == []
    assert cp.apply_core_patches("test") == ()


def test_a_declared_critical_name_with_no_record_refuses(plugin_homes, monkeypatch, refusing):
    """register() raises before it stages anything: only plugin.yaml knows the patch was critical."""
    make, log = plugin_homes
    log.FAIL_BEFORE_STAGING = True
    home = make("a", granted_config(), core_patches_yaml="[{name: shout, critical: true}]")
    monkeypatch.setenv("HERMES_HOME", str(home))
    with pytest.raises(cp.CorePatchRefusal) as refused:
        cp.apply_core_patches("test")  # real discovery through the process manager
    assert "shout:shout is declared critical in plugin.yaml but not registered" in refused.value.message
    assert "boom before staging" in refused.value.message


def test_a_declared_critical_name_of_a_disabled_plugin_is_not_required(plugin_homes, monkeypatch):
    make, log = plugin_homes
    log.FAIL_BEFORE_STAGING = True
    home = make("a", "plugins:\n  enabled: []\n", core_patches_yaml="[{name: shout, critical: true}]")
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert cp.apply_core_patches("test") == ()


def test_plugin_yaml_can_make_a_patch_critical(plugin_homes, monkeypatch, no_discovery, refusing):
    make, log = plugin_homes
    log.SHA = "3" * 64  # drifted
    home = make("a", granted_config(), core_patches_yaml="[{name: shout, critical: true}]")
    _manager(home, monkeypatch).discover_and_load()
    assert _state() == [("shout:shout", cp.SKIPPED_DRIFT)] and cp.records()[0].critical
    with pytest.raises(cp.CorePatchRefusal):
        cp.apply_core_patches("test")


def test_apply_runs_plugin_discovery(monkeypatch):
    import hermes_cli.plugins

    calls = []
    monkeypatch.setattr(hermes_cli.plugins, "discover_plugins", lambda force=False: calls.append(force))
    cp.apply_core_patches("test")
    cp.apply_core_patches("test")
    assert calls == [False, False]


# ── finding 3: the grant must hold in every home, whatever the load order ───────────────────


@pytest.mark.parametrize("order", ["granted-first", "ungranted-first"])
def test_a_patch_is_installed_only_when_every_home_grants_it(plugin_homes, monkeypatch, order):
    make, _ = plugin_homes
    granted, ungranted = make("granted", granted_config()), make("ungranted", "plugins:\n  enabled: [shout]\n")
    module = sys.modules["cpdemo_target"]
    original = module.greet
    homes = [granted, ungranted] if order == "granted-first" else [ungranted, granted]
    for home in homes:
        _manager(home, monkeypatch).discover_and_load()
    assert _state() == [("shout:shout", cp.SKIPPED_NO_GRANT)]
    detail = cp.records()[0].detail
    assert "process-global" in detail and str(ungranted) in detail and str(granted) not in detail
    assert module.greet is original


@pytest.mark.parametrize("order", ["a-first", "b-first"])
def test_two_granted_homes_share_one_install(plugin_homes, monkeypatch, order):
    make, log = plugin_homes
    homes = [make("a", granted_config()), make("b", granted_config())]
    for home in homes if order == "a-first" else homes[::-1]:
        _manager(home, monkeypatch).discover_and_load()
    assert _state() == [("shout:shout", cp.ACTIVE)]
    assert sys.modules["cpdemo_target"].greet("x") == "HELLO X"  # wrapped once, not twice


def test_when_the_ungranted_home_leaves_the_patch_is_installed(target):
    def up(original):
        def greet(name):
            return original(name).upper()
        return greet
    spec = cp.make_spec(plugin_id="demo", name="up", target=f"{target.name}:greet", kind="wrap", fn=up,
                        expect_ast_sha=GREET_SHA)
    for owner, granted in (("home-b", False), ("home-a", True)):
        key = cp.stage(spec, owner, granted=granted, declared=True)
        cp.activate(key, owner)
    assert {r.spec.key: r.status for r in cp.records()}["demo:up"] == cp.SKIPPED_NO_GRANT
    cp.release("demo:up", "home-b")
    assert {r.spec.key: r.status for r in cp.records()}["demo:up"] == cp.ACTIVE
    assert target.mod.greet("x") == "HELLO X"


@pytest.mark.parametrize("value", ['"true"', '"false"', "1", '"True"'])
def test_only_a_literal_true_grants(plugin_homes, value):
    make, _ = plugin_homes
    assert cp.core_patch_granted("shout", make("h", granted_config(value))) is False


def test_another_plugins_grant_does_not_grant(plugin_homes):
    make, _ = plugin_homes
    home = make("h", granted_config(plugin="other"))
    assert cp.core_patch_granted("shout", home) is False
    assert cp.core_patch_granted("other", home) is True


# ── finding 4: a forced reload keeps the patch active; enforcement after startup ─────────────


def test_a_forced_reload_keeps_the_patch_active_throughout(plugin_homes, monkeypatch):
    make, log = plugin_homes
    manager = _manager(make("a", granted_config()), monkeypatch)
    manager.discover_and_load()
    module = sys.modules["cpdemo_target"]
    assert module.greet("x") == "HELLO X"
    manager.discover_and_load(force=True)
    manager.discover_and_load(force=True)
    # register() ran three times; the 2nd and 3rd saw the patch still installed mid-reload
    assert log.CALLS == ["hello x", "HELLO X", "HELLO X"]
    assert module.greet("x") == "HELLO X" and _state() == [("shout:shout", cp.ACTIVE)]


def test_a_reload_the_plugin_does_not_come_back_from_undoes_it(plugin_homes, monkeypatch):
    make, log = plugin_homes
    home = make("a", granted_config())
    manager = _manager(home, monkeypatch)
    manager.discover_and_load()
    module = sys.modules["cpdemo_target"]
    (home / "config.yaml").write_text("plugins:\n  enabled: []\n", encoding="utf-8")
    manager.discover_and_load(force=True)
    assert module.greet("x") == "hello x" and _state() == []


def test_a_critical_patch_failing_on_reload_is_an_error_after_startup(plugin_homes, monkeypatch, no_discovery,
                                                                      caplog):
    caplog.set_level(logging.ERROR, logger="hermes_cli.core_patches")
    make, log = plugin_homes
    log.CRITICAL = True
    manager = _manager(make("a", granted_config()), monkeypatch)
    manager.discover_and_load()
    cp.apply_core_patches("test")  # startup passed
    log.FAIL_AFTER_STAGING = True
    manager.discover_and_load(force=True)
    assert _state() == [("shout:shout", cp.SKIPPED_ERROR)]
    assert any("after startup" in r.getMessage() and "shout:shout" in r.getMessage() for r in caplog.records)


# ── finding 7: no target import during register(), never under the lock ────────────────────

LAZY_TARGET = '''
import threading

from hermes_cli import core_patches as _cp

LOCK_FREE = []


def _probe_lock():
    got = _cp._LOCK.acquire(timeout=2)
    LOCK_FREE.append(got)
    if got:
        _cp._LOCK.release()


_t = threading.Thread(target=_probe_lock)
_t.start()
_t.join()


def greet(name: str) -> str:
    """Say hello."""
    return f"hello {name}"
'''


def _lazy_target(tmp_path, monkeypatch, name):
    (tmp_path / f"{name}.py").write_text(LAZY_TARGET, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(name, None)
    from hermes_cli.core_patches_ast import core_patch_sha

    return core_patch_sha(f"{name}:greet", tmp_path)


def _counting_up(calls):
    def factory(original):
        calls.append(1)

        def greet(name):
            return original(name).upper()
        return greet
    return factory


def test_register_and_publish_do_not_import_the_target(tmp_path, monkeypatch, no_discovery):
    name = "cplazy_a"
    sha = _lazy_target(tmp_path, monkeypatch, name)
    spec = cp.make_spec(plugin_id="demo", name="up", target=f"{name}:greet", kind="wrap", fn=_counting_up([]),
                        expect_ast_sha=sha)
    key = cp.stage(spec, "o", granted=True, declared=True)
    cp.activate(key, "o")
    assert name not in sys.modules
    assert [r.status for r in cp.records()] == [cp.PENDING]
    cp.apply_core_patches("test")
    module = sys.modules[name]
    assert module.LOCK_FREE == [True]  # imported with the registry lock free
    assert module.greet("x") == "HELLO X" and [r.status for r in cp.records()] == [cp.ACTIVE]
    sys.modules.pop(name, None)


def test_two_threads_applying_at_once_install_once(tmp_path, monkeypatch, no_discovery):
    name = "cplazy_b"
    sha = _lazy_target(tmp_path, monkeypatch, name)
    calls = []
    spec = cp.make_spec(plugin_id="demo", name="up", target=f"{name}:greet", kind="wrap", fn=_counting_up(calls),
                        expect_ast_sha=sha)
    cp.activate(cp.stage(spec, "o", granted=True, declared=True), "o")
    barrier = threading.Barrier(2)
    errors = []

    def run():
        try:
            barrier.wait()
            cp.apply_core_patches("test")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == [] and calls == [1]
    assert importlib.import_module(name).greet("x") == "HELLO X"
    assert [r.status for r in cp.records()] == [cp.ACTIVE]
    sys.modules.pop(name, None)
