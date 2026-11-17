"""The chain: factories called once, status matches what is installed, no attributes on shared
callables (review finding 9)."""

from __future__ import annotations

import logging

from hermes_cli import core_patches as cp
from tests.core_patches.conftest import GREET_SHA

OWNER = "home-a"


def _spec(target, name, fn, *, kind="wrap", order=0):
    return cp.make_spec(plugin_id="demo", name=name, target=f"{target.name}:greet", kind=kind, fn=fn,
                        expect_ast_sha=GREET_SHA, order=order)


def _install(spec):
    key = cp.stage(spec, OWNER, granted=True, declared=True)
    cp.activate(key, OWNER)
    return key


def _statuses():
    return {r.spec.name: r.status for r in cp.records()}


def counting(letter, calls):
    def factory(original):
        calls.append(letter)

        def greet(name):
            return letter + original(name)
        return greet
    return factory


def test_each_wrap_factory_is_called_once_across_adds_and_undos(target):
    calls = []
    _install(_spec(target, "a", counting("a", calls), order=0))
    _install(_spec(target, "b", counting("b", calls), order=-5))  # inserted BELOW a
    _install(_spec(target, "c", counting("c", calls), order=5))
    assert target.mod.greet("x") == "cabhello x"
    cp.release("demo:b", OWNER)
    assert target.mod.greet("x") == "cahello x"
    assert sorted(calls) == ["a", "b", "c"]


def test_a_target_replaced_outside_the_registry_reports_what_is_installed(target, caplog):
    caplog.set_level(logging.WARNING, logger="hermes_cli.core_patches")
    original = target.mod.greet
    _install(_spec(target, "a", counting("a", [])))
    assert target.user.greet("x") == "ahello x"  # the from-import alias was rebound

    def external(name):
        return "external"
    target.mod.greet = external
    _install(_spec(target, "b", counting("b", [])))
    assert _statuses() == {"a": cp.SKIPPED_ERROR, "b": cp.SKIPPED_ERROR}
    assert target.mod.greet is external
    assert target.user.greet is original  # nothing the registry installed stays reachable


def test_the_registry_sets_no_attribute_on_a_shared_callable(target):
    def shared(name):
        return f"bonjour {name}"
    before = dict(vars(shared))
    _install(_spec(target, "fr", shared, kind="replace"))
    assert target.mod.greet is shared and vars(shared) == before
    assert cp.installed_patch(shared) == "demo:fr"


def test_an_identity_wrap_does_not_mark_the_original(target):
    original = target.mod.greet
    before = dict(vars(original))
    _install(_spec(target, "same", lambda inner: inner))
    assert vars(original) == before
    assert target.mod.greet("x") == "hello x"
