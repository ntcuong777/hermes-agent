"""Frames are per patch and an original run outside the call's context is never run a second time
(hermes_cli/core_patches_shell.py)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_cli import core_patches_shell as shell


def build(make, original, *, key="t:x", flavor=shell.SYNC):
    """The installed callable of one wrap patch (``make`` receives the trampoline) and its guard."""
    guard = shell.Guard(key, "wrap", 3, (), None)

    def inner():
        return original

    candidate = make(shell.trampoline(guard, inner, original, flavor))
    return shell.make_shell(guard, candidate, inner, flavor), guard


def _outcome(call):
    try:
        return ("value", call())
    except RuntimeError as exc:
        return ("raised", str(exc))


def test_an_original_run_on_a_thread_pool_is_not_run_a_second_time_when_the_wrapper_then_raises():
    calls = []

    def original(x):
        calls.append(x)
        return x + 1

    def make(orig):
        def wrapper(x):
            ThreadPoolExecutor().submit(orig, x).result()
            raise RuntimeError("after the pool")
        return wrapper

    f, guard = build(make, original)
    assert _outcome(lambda: f(1)) == ("raised", "after the pool")
    assert calls == [1]
    assert guard.total == 1


def test_an_original_run_on_a_plain_thread_is_not_run_a_second_time_when_the_wrapper_then_raises():
    calls = []

    def original(x):
        calls.append(x)
        return x + 1

    def make(orig):
        def wrapper(x):
            worker = threading.Thread(target=orig, args=(x,))
            worker.start()
            worker.join(5)
            raise RuntimeError("after the thread")
        return wrapper

    f, guard = build(make, original)
    assert _outcome(lambda: f(1)) == ("raised", "after the thread")
    assert calls == [1]
    assert guard.total == 1


@pytest.mark.parametrize("via", ["shell", "trampoline"])
def test_a_patch_failing_before_its_own_original_falls_back_to_its_own_original_not_another_patchs_result(via):
    base_calls, g_calls = [], []

    def base(x):
        base_calls.append(x)
        return x

    # patch f: wraps base and returns base's result plus one; keeps the trampoline it was given
    f_saved = []

    def make_f(orig):
        f_saved.append(orig)

        def f_wrapper(x):
            return orig(x) + 1
        return f_wrapper

    f_shell, f_guard = build(make_f, base, key="t:f")

    # patch g (its own guard): its wrapper calls f (through f's shell, or through the trampoline f saved,
    # which runs while g's frame is current) and then raises before calling its own original
    def original_g(x):
        g_calls.append(x)
        return x + 100

    def make_g(orig):
        def g_wrapper(x):
            (f_shell if via == "shell" else f_saved[0])(x)
            raise RuntimeError("g bug before its own original")
        return g_wrapper

    g_shell, g_guard = build(make_g, original_g, key="t:g")

    assert g_shell(5) == 105  # g's fallback answer is g's original's result, not f's
    assert g_calls == [5]
    assert base_calls == [5]
    assert (g_guard.total, f_guard.total) == (1, 0)


def test_the_shell_has_the_signature_and_name_of_the_original_not_of_a_star_args_wrapper():
    import inspect

    def original(a, b=2):
        return a + b

    guard = shell.Guard("t:sig", "wrap", 3, (), None)

    def inner():
        return original

    def wrapper(*args, **kwargs):
        return original(*args, **kwargs)
    candidate = wrapper
    installed = shell.make_shell(guard, candidate, inner, shell.SYNC, original=original)
    assert inspect.signature(installed) == inspect.signature(original)
    assert installed.__name__ == "original"
