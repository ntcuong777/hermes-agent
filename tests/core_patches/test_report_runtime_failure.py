"""report_runtime_failure: a wrap counts an exception it handled itself in the patch's failure streak (the same
streak the safety shell keeps). 3 in a row switch the patch off; a critical patch with a streak is in
critical_problems() and the home-channel notice; an unknown patch is ignored; it never raises and never waits."""

from __future__ import annotations

import threading
import time

import pytest

from hermes_cli import core_patches as cp
from hermes_cli import core_patches_shell as shell
from tests.core_patches.conftest import GREET_SHA
from tests.core_patches.test_registry import _status, up


class Refused(Exception):
    """A deliberate refusal the wrap raises on purpose (declared in propagates=)."""


def _copy_wrap(state, *, refuse):
    """A wrap whose own code fails while ``state["broken"]``: it counts the failure and answers the call itself
    (``refuse=True``: a declared refusal; ``refuse=False``: the original's answer)."""
    def factory(original):
        def greet(name):
            try:
                if state["broken"]:
                    raise KeyError("copy broke")
                return original(name).upper()
            except KeyError as exc:
                cp.report_runtime_failure("demo", "copy", exc)
                if refuse:
                    raise Refused("not_found") from None
                return original(name)
        return greet
    return factory


def _install_copy(target, *, refuse=True, critical=False):
    state = {"broken": False}
    spec = cp.make_spec(plugin_id="demo", name="copy", target=f"{target.name}:greet", kind="wrap",
                        fn=_copy_wrap(state, refuse=refuse), expect_ast_sha=GREET_SHA, order=0,
                        critical=critical, propagates=(Refused,))
    key = cp.stage(spec, "owner-copy", granted=True, declared=True)
    cp.activate(key, "owner-copy")
    return key, state


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_a_handled_failure_is_counted_on_the_patch_streak(target):
    key, state = _install_copy(target, refuse=False)
    state["broken"] = True
    assert target.mod.greet("x") == "hello x"  # the wrap answered with the original's result
    total, streak, last = cp.failure_stats()[key]
    assert (total, streak) == (1, 1) and last.startswith("KeyError")


def test_three_handled_refusals_in_a_row_switch_the_patch_off(target, caplog):
    key, state = _install_copy(target, refuse=True)
    state["broken"] = True
    for _ in range(3):
        with pytest.raises(Refused):
            target.mod.greet("x")
    assert _wait_for(lambda: _status(key) == cp.DISABLED)
    assert target.mod.greet("x") == "hello x"  # switched off: the original answers from now on, no refusal
    assert cp.failure_stats()[key][:2] == (3, 3)


def test_a_clean_call_after_a_handled_failure_resets_the_streak(target):
    key, state = _install_copy(target, refuse=False)
    state["broken"] = True
    assert target.mod.greet("x") == "hello x"
    assert cp.failure_stats()[key][1] == 1
    state["broken"] = False
    assert target.mod.greet("x") == "HELLO X"
    assert cp.failure_stats()[key][1] == 0


def test_an_unknown_patch_is_ignored_and_nothing_changes(target):
    key, _state = _install_copy(target)
    before = cp.failure_stats()
    cp.report_runtime_failure("nobody", "nothing", KeyError("x"))
    assert cp.failure_stats() == before
    assert _status(key) != cp.DISABLED


@pytest.mark.parametrize("plugin_id, name, exc", [
    (None, None, None),
    ("demo", object(), ValueError("x")),
    ("demo", "copy", "not an exception"),
])
def test_bad_arguments_never_raise(target, plugin_id, name, exc):
    _install_copy(target)
    cp.report_runtime_failure(plugin_id, name, exc)  # must return None


def test_an_internal_failure_is_swallowed(target, monkeypatch):
    key, state = _install_copy(target, refuse=False)

    def boom(guard, exc, where):
        raise RuntimeError("accounting broke")
    monkeypatch.setattr(shell, "count_handled", boom)
    cp.report_runtime_failure("demo", "copy", KeyError("x"))  # must not raise
    assert cp.failure_stats()[key][1] == 0


def test_the_counter_never_waits_for_the_registry_lock(target):
    key, _state = _install_copy(target, refuse=False)
    holder_ready, release = threading.Event(), threading.Event()

    def hold():
        with cp._LOCK:
            holder_ready.set()
            release.wait(5)
    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    holder_ready.wait(2)
    try:
        started = time.monotonic()
        cp.report_runtime_failure("demo", "copy", KeyError("x"))
        assert time.monotonic() - started < 1.0
    finally:
        release.set()
        thread.join(2)


def test_a_critical_patch_with_a_streak_is_in_the_problems_and_the_notice(target, no_discovery):
    key, state = _install_copy(target, refuse=False, critical=True)
    assert not any(key in line for line in cp.critical_problems())
    state["broken"] = True
    target.mod.greet("x")
    problems = cp.critical_problems()
    assert any(line.startswith(f"{key} on ") and "1 runtime failure(s) in a row" in line for line in problems), problems
    notice = cp.critical_notice_lines()
    assert f"{key} is active with runtime failures in a row (KeyError)" in notice, notice


def test_a_non_critical_patch_with_a_streak_is_not_loud(target, no_discovery):
    key, state = _install_copy(target, refuse=False, critical=False)
    state["broken"] = True
    target.mod.greet("x")
    assert cp.failure_stats()[key][1] == 1
    assert not any(key in line for line in cp.critical_problems())
    assert not any(key in line for line in cp.critical_notice_lines())
