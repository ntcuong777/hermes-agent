"""The fingerprint describes the code this process runs (review finding 2)."""

from __future__ import annotations

import importlib
import sys
import types

from hermes_cli import core_patches as cp
from hermes_cli.core_patches_ast import core_patch_sha
from tests.core_patches.conftest import FIXTURES, GREET_SHA

OWNER = "home-a"


def up(original):
    def greet(name):
        return original(name).upper()
    return greet


def _register(target_name, *, name="up", owner=OWNER, sha=GREET_SHA):
    spec = cp.make_spec(plugin_id="demo", name=name, target=f"{target_name}:greet", kind="wrap", fn=up,
                        expect_ast_sha=sha)
    key = cp.stage(spec, owner, granted=True, declared=True)
    cp.activate(key, owner)
    return {r.spec.key: r for r in cp.records()}[key]


def test_a_file_rewritten_after_import_is_drift_on_reload(target):
    assert _register(target.name).status == cp.ACTIVE
    # `hermes update` rewrites the checkout under a running process; the hash of the new file is
    # unchanged (a comment), but this process still runs the code it imported
    target.path.write_text(target.path.read_text(encoding="utf-8") + "\n# updated\n", encoding="utf-8")
    record = _register(target.name)  # the plugin hot-reloads: a new registration by the same owner
    assert record.status == cp.SKIPPED_DRIFT and "source changed since import" in record.detail
    assert target.mod.greet("x") == "hello x"


def test_a_def_rebound_later_in_the_module_is_drift(tmp_path, monkeypatch):
    text = (FIXTURES / "cpdemo_target.py").read_text(encoding="utf-8")
    (tmp_path / "cprebound.py").write_text(text + "\n\ndef _other(name):\n    return 'other'\n\n\ngreet = _other\n",
                                           encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("cprebound", None)
    module = importlib.import_module("cprebound")
    assert core_patch_sha("cprebound:greet") == GREET_SHA  # the def text alone says "unchanged"
    record = _register("cprebound")
    assert record.status == cp.SKIPPED_DRIFT and "is not the def" in record.detail
    assert module.greet("x") == "other"
    sys.modules.pop("cprebound", None)


def test_a_def_that_moved_since_import_is_drift(target):
    # rewritten before the registry first saw it: the line check catches what the stat cannot
    text = target.path.read_text(encoding="utf-8")
    target.path.write_text(text.replace("def greet", "\n\n\ndef greet", 1), encoding="utf-8")
    record = _register(target.name)
    assert record.status == cp.SKIPPED_DRIFT and "line" in record.detail


def test_the_hash_reads_the_file_the_module_was_loaded_from(tmp_path, monkeypatch):
    path = tmp_path / "elsewhere.py"
    path.write_text((FIXTURES / "cpdemo_target.py").read_text(encoding="utf-8"), encoding="utf-8")
    module = types.ModuleType("batch_runner")  # a module that also exists in the checkout root
    module.__file__ = str(path)
    monkeypatch.setitem(sys.modules, "batch_runner", module)
    assert core_patch_sha("batch_runner:greet") == GREET_SHA
