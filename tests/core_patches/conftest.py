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


@pytest.fixture(autouse=True)
def _clean_registry():
    from hermes_cli import core_patches

    core_patches._reset_for_tests()
    yield
    core_patches._reset_for_tests()


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
