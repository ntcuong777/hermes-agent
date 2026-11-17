"""Fixtures for the core-patch registry tests: a fresh copy of the fixture target per test."""

from __future__ import annotations

import importlib
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GREET_SHA = "8ac9a553d27b157324c2b1e0477f32ed396d2195aec4e09bb1a95cca39ff219a"
AGREET_SHA = "71d8782ef89c9b922a051af785dd8a9080416a44c42c27f1e08375f0950e3362"
BOX_SIZE_SHA = "19e7a1378beccf0d25ea81296a070de4ff76a617137d994bfb9850326b7bb045"
BOX_MAKE_SHA = "f7c8b0832fe6eb843660fdf3d6fb4bee3cc3e2d43553a54bf246fde8ba285196"
CHANGED_GREET_SHA = "030a2655c8f56f9c6fe15e0be92e866e157a568b84c6c723ff06a7aef75cf3f0"
# A complete provenance record, as `hermes plugins patches --record` writes it (passed to write_lock_entry).
PROVENANCE = {
    "plugin": "demo", "kind": "wrap", "reason": "demo: upper-case the greeting",
    "fork_head": "8fa8efda663b6a59076d47586a41a8a1df039873", "fork_dirty": False,
    "upstream_ref": {"commit": "e96221fa73d2dac7ba9f3bc5a8b866b3d0fc188e", "release": "0.23.0", "date": "2026-11-16"},
    "target_file_blob_sha": "", "fork_file_blob_sha": "", "patch_fn": "__init__.py:_shout", "copy_of": None,
}


@pytest.fixture(autouse=True)
def _clean_registry():
    from hermes_cli import core_patches

    core_patches._reset_for_tests()
    yield
    core_patches._reset_for_tests()


@pytest.fixture
def refusing(monkeypatch):
    """`core_patches.refuse_on_critical: true` for the test (the default is false: nothing refuses)."""
    from hermes_cli import core_patches_policy as policy

    monkeypatch.setattr(policy, "load_settings", lambda: policy.Settings(refuse_on_critical=True))


@pytest.fixture
def target(tmp_path, monkeypatch):
    """A uniquely named copy of fixtures/cpdemo_target.py, plus a module that did
    `from <it> import greet` before any patch, plus a module that holds the original on purpose."""
    tag = uuid.uuid4().hex[:8]
    name, user, holder = f"cpt_{tag}", f"cpu_{tag}", f"cph_{tag}"
    (tmp_path / f"{name}.py").write_text((FIXTURES / "cpdemo_target.py").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / f"{user}.py").write_text(f"from {name} import greet\n", encoding="utf-8")
    (tmp_path / f"{holder}.py").write_text(
        f"from {name} import greet as ORIGINAL\n\n\ndef up(original):\n"
        "    def greet(n):\n        return original(n).upper()\n    return greet\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    modules = SimpleNamespace(
        name=name, mod=importlib.import_module(name), user=importlib.import_module(user),
        holder=importlib.import_module(holder), path=tmp_path / f"{name}.py")
    yield modules
    for module in (name, user, holder):
        sys.modules.pop(module, None)


@pytest.fixture
def no_discovery(monkeypatch):
    """apply_core_patches() without a real plugin sweep."""
    import hermes_cli.plugins

    monkeypatch.setattr(hermes_cli.plugins, "discover_plugins", lambda force=False: None)


SHOUT_PLUGIN = '''
import functools

import {log} as LOG


def _shout(original):
    LOG.FACTORY_CALLS.append(1)

    @functools.wraps(original)
    def greet(name):
        return original(name).upper()

    return greet


def register(ctx):
    import cpdemo_target

    LOG.CALLS.append(cpdemo_target.greet("x"))  # what the target does WHILE this plugin (re)loads
    if LOG.FAIL_BEFORE_STAGING:
        raise RuntimeError("boom before staging")
    ctx.register_core_patch("shout", target="cpdemo_target:greet", kind="wrap", fn=_shout,
                            expect_ast_sha=LOG.SHA, critical=LOG.CRITICAL)
    if LOG.FAIL_AFTER_STAGING:
        raise RuntimeError("boom after staging")
'''


@pytest.fixture
def plugin_homes(tmp_path, monkeypatch):
    """Factory: ``make(label, config_text, core_patches_yaml)`` -> (home, log module). Every home holds
    the ``shout`` plugin (wraps cpdemo_target.greet); the shared log module steers and records it."""
    tag = uuid.uuid4().hex[:8]
    log_name = f"cplog_{tag}"
    (tmp_path / f"{log_name}.py").write_text(
        f"SHA = {GREET_SHA!r}\nCRITICAL = False\nFAIL_BEFORE_STAGING = False\nFAIL_AFTER_STAGING = False\n"
        "CALLS = []\nFACTORY_CALLS = []\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.syspath_prepend(str(FIXTURES))
    sys.modules.pop("cpdemo_target", None)
    importlib.import_module("cpdemo_target")
    log = importlib.import_module(log_name)

    def make(label, config_text, core_patches_yaml="[shout]"):
        home = tmp_path / label
        plugin = home / "plugins" / "shout"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text(
            f"name: shout\nversion: 0.1.0\ndescription: test\nauthor: custom\ncore_patches: {core_patches_yaml}\n",
            encoding="utf-8")
        (plugin / "__init__.py").write_text(SHOUT_PLUGIN.format(log=log_name), encoding="utf-8")
        (home / "config.yaml").write_text(config_text, encoding="utf-8")
        return home

    yield make, log
    sys.modules.pop("cpdemo_target", None)
    sys.modules.pop(log_name, None)


def granted_config(value="true", plugin="shout"):
    return f"plugins:\n  enabled: [shout]\n  entries:\n    {plugin}:\n      allow_core_patch: {value}\n"
