"""``kind="replace"``: which exceptions of a replacement body fall back to the original, by
``core_patches.replace_fallback`` (hermes_cli/core_patches_shell.py, hermes_cli/core_patches_policy.py)."""

from __future__ import annotations

import pytest

from hermes_cli import core_patches_policy as policy
from hermes_cli import core_patches_shell as shell


def build(replacement, original, mode=None):
    calls = []

    def recorded(x):
        calls.append(x)
        return original(x)

    guard = shell.Guard("t:r", "replace", 3, (), None, replace_fallback=mode or "bug-class")
    return shell.make_shell(guard, replacement, lambda: recorded, shell.SYNC), guard, calls


def _plus_one(x):
    return x + 1


def _raiser(exc_type):
    def replacement(x):
        raise exc_type("replacement bug")
    return replacement


def test_default_mode_propagates_a_legitimate_error_and_does_not_run_the_original():
    f, guard, calls = build(_raiser(ValueError), _plus_one)
    with pytest.raises(ValueError):
        f(1)
    assert calls == []
    assert guard.consecutive == 0


def test_default_mode_falls_back_to_the_original_for_a_bug_class_error():
    f, guard, calls = build(_raiser(TypeError), _plus_one)
    assert f(1) == 2
    assert calls == [1]
    assert guard.total == 1


@pytest.mark.parametrize("bug", [TypeError, AttributeError, NameError, ImportError, AssertionError,
                                 NotImplementedError])
def test_every_bug_class_error_falls_back_in_default_mode(bug):
    f, guard, calls = build(_raiser(bug), _plus_one)
    assert f(1) == 2 and calls == [1]


def test_always_mode_falls_back_for_a_value_error():
    f, guard, calls = build(_raiser(ValueError), _plus_one, mode="always")
    assert f(1) == 2
    assert calls == [1]
    assert guard.total == 1


def test_never_mode_propagates_a_bug_class_error_without_calling_the_original():
    f, guard, calls = build(_raiser(TypeError), _plus_one, mode="never")
    with pytest.raises(TypeError):
        f(1)
    assert calls == []
    assert guard.total == 1


@pytest.mark.parametrize("value, expected", [
    ("always", "always"), ("never", "never"), ("bug-class", "bug-class"),
    ("bogus", "bug-class"), (None, "bug-class"), (True, "bug-class"),
])
def test_the_policy_reads_replace_fallback_and_maps_anything_else_to_bug_class(value, expected):
    section = {} if value is None else {"replace_fallback": value}
    assert policy.parse_settings(section).replace_fallback == expected
