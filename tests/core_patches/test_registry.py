"""The core-patch registry on a fake target module (hermes_cli/core_patches.py)."""

from __future__ import annotations

import asyncio
import functools
import logging

import pytest

from hermes_cli import core_patches as cp
from tests.core_patches.conftest import AGREET_SHA, BOX_MAKE_SHA, BOX_SIZE_SHA, GREET_SHA

OWNER = "home-a"


def _spec(target, name, fn, *, kind="wrap", sha=GREET_SHA, attr="greet", order=0, critical=False, plugin="demo"):
    return cp.make_spec(plugin_id=plugin, name=name, target=f"{target.name}:{attr}", kind=kind, fn=fn,
                        expect_ast_sha=sha, order=order, critical=critical)


def _install(spec, *, granted=True, declared=True, owner=OWNER):
    key = cp.stage(spec, owner, granted=granted, declared=declared)
    cp.activate(key, owner)
    return key


def _status(key):
    return {r.spec.key: r.status for r in cp.records()}[key]


def up(original):
    @functools.wraps(original)
    def greet(name):
        return original(name).upper()
    return greet


def bang(original):
    def greet(name):
        return original(name) + "!"
    return greet


def test_wrap_sync_installs_and_calls_through(target):
    key = _install(_spec(target, "up", up))
    assert target.mod.greet("x") == "HELLO X"
    assert _status(key) == cp.ACTIVE
    assert cp.installed_patch(target.mod.greet) == "demo:up"


def test_wrap_async_returns_an_awaitable_result(target):
    def aup(original):
        async def agreet(name):
            return (await original(name)).upper()
        return agreet
    key = _install(_spec(target, "aup", aup, sha=AGREET_SHA, attr="agreet"))
    assert asyncio.run(target.mod.agreet("x")) == "HELLO X"
    assert _status(key) == cp.ACTIVE


def test_async_target_refuses_a_sync_wrapper(target):
    original = target.mod.agreet
    key = _install(_spec(target, "bad", up, sha=AGREET_SHA, attr="agreet"))
    assert _status(key) == cp.SKIPPED_ERROR
    assert target.mod.agreet is original


def test_replace_swaps_the_function(target):
    def greet(name):
        return f"bonjour {name}"
    key = _install(_spec(target, "fr", greet, kind="replace"))
    assert target.mod.greet("x") == "bonjour x"
    assert _status(key) == cp.ACTIVE


def test_a_second_replace_on_one_target_is_refused(target):
    first = _install(_spec(target, "one", lambda name: "one", kind="replace"))
    second = _install(_spec(target, "two", lambda name: "two", kind="replace", plugin="other"))
    assert target.mod.greet("x") == "one"
    assert _status(first) == cp.ACTIVE and _status(second) == cp.SKIPPED_ERROR
    assert "already replaced by demo:one" in {r.spec.key: r.detail for r in cp.records()}["other:two"]


def prefix(letter):
    """A wrap that prepends ``letter``: wraps of this kind do NOT commute, so the order is visible."""
    def factory(original):
        def greet(name):
            return letter + original(name)
        return greet
    return factory


def test_wraps_stack_by_order_then_registration(target):
    _install(_spec(target, "outer", prefix("o"), order=10))
    _install(_spec(target, "inner", prefix("i"), order=-5))
    _install(_spec(target, "middle", prefix("m"), order=0))
    # inner (lowest order) wraps the original first; outer runs first: o(m(i(original)))
    assert target.mod.greet("x") == "omihello x"
    _install(_spec(target, "late", prefix("l"), order=0))  # same order as "middle", registered later -> outside it
    assert target.mod.greet("x") == "olmihello x"
    assert [r.spec.name for r in cp.records()] == ["inner", "middle", "late", "outer"]


def test_a_replace_is_innermost_and_wraps_run_over_it(target):
    _install(_spec(target, "outer", prefix("o"), order=10))
    _install(_spec(target, "fr", lambda name: f"bonjour {name}", kind="replace"))
    assert target.mod.greet("x") == "obonjour x"


def test_drift_is_skipped_and_logged_once(target, caplog):
    caplog.set_level(logging.WARNING, logger="hermes_cli.core_patches")
    original = target.mod.greet
    key = _install(_spec(target, "old", up, sha="0" * 64))
    cp.release(key, OWNER)
    key = _install(_spec(target, "old", up, sha="0" * 64))
    assert _status(key) == cp.SKIPPED_DRIFT
    assert target.mod.greet is original
    assert len([r for r in caplog.records if "demo:old skipped-drift" in r.getMessage()]) == 1


def test_no_grant_is_skipped_and_core_untouched(target):
    original = target.mod.greet
    key = _install(_spec(target, "up", up), granted=False)
    assert _status(key) == cp.SKIPPED_NO_GRANT
    assert target.mod.greet is original
    assert "allow_core_patch: true" in cp.records()[0].detail


def test_an_undeclared_patch_is_an_error(target):
    key = _install(_spec(target, "up", up), declared=False)
    assert _status(key) == cp.SKIPPED_ERROR
    assert "core_patches:" in cp.records()[0].detail


def test_critical_drift_refuses_startup(target, no_discovery):
    _install(_spec(target, "must", up, sha="1" * 64, critical=True))
    with pytest.raises(cp.CorePatchRefusal) as refused:
        cp.apply_core_patches("test")
    assert refused.value.code == 78
    assert "demo:must" in refused.value.message and "skipped-drift" in refused.value.message


def test_critical_without_grant_does_not_refuse(target, no_discovery):
    _install(_spec(target, "must", up, critical=True), granted=False)
    assert [r.status for r in cp.apply_core_patches("test")] == [cp.SKIPPED_NO_GRANT]


def test_from_import_aliases_are_rebound_and_restored(target):
    original = target.mod.greet
    key = _install(_spec(target, "up", up))
    assert target.user.greet("x") == "HELLO X"
    cp.release(key, OWNER)
    assert target.user.greet is original and target.mod.greet is original


def test_the_module_that_defines_the_wrap_keeps_the_original(target):
    original = target.mod.greet
    _install(_spec(target, "up", target.holder.up))
    assert target.holder.ORIGINAL is original  # rebinding it would make the wrapper call itself
    assert target.mod.greet("x") == "HELLO X"


def test_undo_restores_the_original_object(target):
    original = target.mod.greet
    key = _install(_spec(target, "up", up))
    assert target.mod.greet is not original
    cp.release(key, OWNER)
    assert target.mod.greet is original
    assert cp.records() == ()


def test_double_activate_and_apply_are_idempotent(target, no_discovery):
    calls = []

    def counting(original):
        def greet(name):
            calls.append(name)
            return original(name)
        return greet
    key = _install(_spec(target, "count", counting))
    installed = target.mod.greet
    cp.activate(key, OWNER)
    cp.apply_core_patches("test")
    cp.apply_core_patches("test")
    assert target.mod.greet is installed
    target.mod.greet("x")
    assert calls == ["x"]


def test_a_target_replaced_outside_the_registry_is_left_alone(target, caplog):
    caplog.set_level(logging.WARNING, logger="hermes_cli.core_patches")
    key = _install(_spec(target, "up", up))

    def external(name):
        return "external"
    target.mod.greet = external
    cp.release(key, OWNER)
    assert target.mod.greet is external
    assert any("could not undo" in r.getMessage() for r in caplog.records)


def test_methods_and_staticmethods_are_patched_on_the_class(target):
    def bigger(original):
        def size(self):
            return original(self) + 10
        return size

    def tagged(original):
        def make():
            box = original()
            box.tag = "made"
            return box
        return make
    size_key = _install(_spec(target, "size", bigger, sha=BOX_SIZE_SHA, attr="Box.size"))
    make_key = _install(_spec(target, "make", tagged, sha=BOX_MAKE_SHA, attr="Box.make"))
    assert target.mod.Box().size() == 11
    assert target.mod.Box.make().tag == "made"
    assert isinstance(vars(target.mod.Box)["make"], staticmethod)
    cp.release(make_key, OWNER)
    cp.release(size_key, OWNER)
    assert target.mod.Box().size() == 1 and not hasattr(target.mod.Box.make(), "tag")


def test_an_inherited_method_is_an_error(target):
    key = _install(_spec(target, "sub", up, sha=BOX_SIZE_SHA, attr="Sub.size"))
    assert _status(key) == cp.SKIPPED_ERROR
    assert "not defined on Sub itself" in cp.records()[0].detail


@pytest.mark.parametrize("kwargs, error", [
    ({"name": "Bad Name"}, ValueError),
    ({"target": "no-colon"}, ValueError),
    ({"kind": "patch"}, ValueError),
    ({"fn": "not callable"}, TypeError),
    ({"expect_ast_sha": "abc"}, ValueError),
    ({"order": True}, TypeError),
    ({"critical": "yes"}, TypeError),
])
def test_bad_arguments_raise(kwargs, error):
    base = {"plugin_id": "demo", "name": "ok", "target": "pkg.mod:fn", "kind": "wrap", "fn": up,
            "expect_ast_sha": GREET_SHA}
    with pytest.raises(error):
        cp.make_spec(**{**base, **kwargs})


def test_a_wrap_factory_that_raises_is_a_skip_not_a_crash(target):
    def broken(original):
        raise RuntimeError("boom")
    original = target.mod.greet
    key = _install(_spec(target, "broken", broken))
    assert _status(key) == cp.SKIPPED_ERROR
    assert "boom" in cp.records()[0].detail
    assert target.mod.greet is original


def test_two_owners_share_one_install_and_the_last_release_undoes_it(target):
    original = target.mod.greet
    key = _install(_spec(target, "up", up), owner="home-a")
    installed = target.mod.greet
    assert cp.stage(_spec(target, "up", up), "home-b", granted=True, declared=True) == key
    cp.activate(key, "home-b")
    assert target.mod.greet is installed
    cp.release(key, "home-a")  # home-b's registration takes over (home-a's plugin module is going away)
    assert target.mod.greet("x") == "HELLO X" and _status(key) == cp.ACTIVE
    cp.release(key, "home-b")
    assert target.mod.greet is original
