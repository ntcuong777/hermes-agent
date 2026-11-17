"""A real plugin (fixtures/demo-core-patch) through the real PluginManager: load wraps, unload undoes."""

from __future__ import annotations

import importlib
import importlib.util
import shutil
import sys

import pytest

from hermes_cli import core_patches as cp
from tests.core_patches.conftest import FIXTURES, GREET_SHA

CONFIG_GRANTED = (
    "plugins:\n  enabled: [demo-core-patch]\n  entries:\n    demo-core-patch:\n      allow_core_patch: true\n"
)
CONFIG_NO_GRANT = "plugins:\n  enabled: [demo-core-patch]\n"


@pytest.fixture
def demo_home(tmp_path, monkeypatch):
    def make(config_text):
        home = tmp_path / "hermes_home"
        (home / "plugins").mkdir(parents=True)
        shutil.copytree(FIXTURES / "demo-core-patch", home / "plugins" / "demo-core-patch")
        (home / "config.yaml").write_text(config_text, encoding="utf-8")
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.syspath_prepend(str(FIXTURES))
        sys.modules.pop("cpdemo_target", None)
        return importlib.import_module("cpdemo_target")
    yield make
    sys.modules.pop("cpdemo_target", None)


def test_a_real_plugin_wraps_through_the_real_loader_and_a_targeted_unload_undoes_it(demo_home):
    from hermes_cli.plugins import PluginManager

    module = demo_home(CONFIG_GRANTED)
    original = module.greet
    manager = PluginManager()
    manager.discover_and_load()
    assert [(r.spec.key, r.status) for r in cp.records()] == [("demo-core-patch:shout", cp.ACTIVE)]
    assert module.greet("x") == "HELLO X"
    manager.unload("demo-core-patch")  # disable / uninstall
    assert module.greet is original
    assert cp.records() == ()


def test_an_unload_all_parks_the_patch_like_other_persistent_registrations(demo_home):
    from hermes_cli.plugins import PluginManager

    module = demo_home(CONFIG_GRANTED)
    manager = PluginManager()
    manager.discover_and_load()
    manager.unload()  # what a forced re-discovery does first
    assert module.greet("x") == "HELLO X"
    assert [r.status for r in cp.records()] == [cp.ACTIVE]


def test_without_the_grant_the_core_is_untouched(demo_home):
    from hermes_cli.plugins import PluginManager

    module = demo_home(CONFIG_NO_GRANT)
    original = module.greet
    PluginManager().discover_and_load()
    assert [(r.spec.key, r.status) for r in cp.records()] == [("demo-core-patch:shout", cp.SKIPPED_NO_GRANT)]
    assert module.greet is original


def test_the_registrar_is_ignored_after_an_abandoned_load():
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest

    ctx = PluginContext(PluginManifest(name="late", key="late", source="user"), PluginManager())
    ctx._abandon_load()
    assert ctx.register_core_patch("x", target="pkg.mod:fn", kind="wrap", fn=lambda o: o,
                                   expect_ast_sha=GREET_SHA) is None
    assert cp.records() == ()


def test_the_demo_plugins_literal_is_the_fixtures_fingerprint():
    spec = importlib.util.spec_from_file_location("demo_core_patch_init", FIXTURES / "demo-core-patch" / "__init__.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    from hermes_cli.core_patches_ast import core_patch_sha

    assert demo.EXPECT_GREET_SHA == core_patch_sha("cpdemo_target:greet", FIXTURES) == GREET_SHA


def test_assert_core_patches_current_passes_then_fails_on_drift(tmp_path):
    from hermes_cli.core_patches_testing import assert_core_patches_current

    assert_core_patches_current(FIXTURES / "demo-core-patch", expected={"shout": GREET_SHA}, source_root=FIXTURES)
    with pytest.raises(pytest.fail.Exception, match="differ from"):
        assert_core_patches_current(FIXTURES / "demo-core-patch", expected={"shout": "0" * 64}, source_root=FIXTURES)
    root = tmp_path / "src"
    shutil.copytree(FIXTURES, root)
    (root / "cpdemo_target.py").write_text("def greet(name):\n    return name\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="core patch drift"):
        assert_core_patches_current(root / "demo-core-patch", source_root=root)
