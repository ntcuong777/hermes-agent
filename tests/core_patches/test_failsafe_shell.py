"""The safety shell: a patch that raises at call time never breaks the call, whatever the original
raises still propagates (hermes_cli/core_patches_shell.py)."""

from __future__ import annotations

import asyncio
import inspect
import time

import pytest

from hermes_cli import core_patches as cp
from hermes_cli import core_patches_shell as shell
from hermes_cli.core_patches_ast import core_patch_sha
from tests.core_patches.conftest import FIXTURES, GREET_SHA


def build(kind, flavor, make, original, *, max_failures=3, propagates=()):
    """The installed callable of one patch (a wrap factory or a replacement) and its guard."""
    guard = shell.Guard("t:x", kind, max_failures, propagates, None)

    def inner():
        return original

    candidate = make(shell.trampoline(guard, inner, original, flavor)) if kind == "wrap" else make
    return shell.make_shell(guard, candidate, inner, flavor), guard


def _plus_one(calls):
    def original(x):
        calls.append(x)
        return x + 1
    return original


# ── sync wrap ──────────────────────────────────────────────────────────────────────────────


def test_a_wrapper_that_raises_before_calling_the_original_is_answered_by_the_original():
    calls = []

    def make(original):
        def wrapper(x):
            raise RuntimeError("patch bug")
        return wrapper
    f, guard = build("wrap", shell.SYNC, make, _plus_one(calls))
    assert f(1) == 2 and calls == [1]
    assert (guard.total, guard.consecutive, guard.disabled) == (1, 1, False)


def test_a_wrapper_that_raises_after_the_original_returned_gets_the_original_result_and_no_second_run():
    calls = []

    def make(original):
        def wrapper(x):
            original(x)
            raise KeyError("post-processing bug")
        return wrapper
    f, guard = build("wrap", shell.SYNC, make, _plus_one(calls))
    assert f(1) == 2 and calls == [1]  # the original ran once: its side effects are not repeated
    assert guard.total == 1


def test_an_exception_from_the_original_propagates_and_is_not_counted():
    calls = []

    def original(x):
        calls.append(x)
        raise ValueError("the original raised")

    def passthrough(inner):
        def wrapper(x):
            return inner(x)
        return wrapper

    def translate(inner):
        def wrapper(x):
            try:
                return inner(x)
            except ValueError as exc:
                raise RuntimeError("translated") from exc
        return wrapper
    for make, message in ((passthrough, "the original raised"), (translate, "translated")):
        f, guard = build("wrap", shell.SYNC, make, original)
        with pytest.raises((ValueError, RuntimeError), match=message):
            f(1)
        assert guard.total == 0
    assert calls == [1, 1]  # once per call, never retried


def test_an_exception_the_patch_declares_propagates_and_an_undeclared_one_is_answered_by_the_original():
    calls = []

    def make(original):
        def wrapper(x):
            raise PermissionError("blocked on purpose")
        return wrapper
    declared, guard = build("wrap", shell.SYNC, make, _plus_one(calls), propagates=(PermissionError,))
    with pytest.raises(PermissionError):
        declared(1)
    assert guard.total == 0 and calls == []
    undeclared, guard = build("wrap", shell.SYNC, make, _plus_one(calls))
    assert undeclared(1) == 2 and guard.total == 1


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_a_base_exception_always_propagates(exc):
    def make(original):
        def wrapper(x):
            raise exc()
        return wrapper
    f, guard = build("wrap", shell.SYNC, make, _plus_one([]))
    with pytest.raises(exc):
        f(1)
    assert guard.total == 0


def test_max_failures_in_a_row_switch_the_patch_off_and_a_success_resets_the_count():
    wrapper_calls = []
    plan = iter([True, False, True, True, True, False])

    def make(original):
        def wrapper(x):
            wrapper_calls.append(x)
            if next(plan):
                raise RuntimeError("flaky")
            return original(x) * 10
        return wrapper
    f, guard = build("wrap", shell.SYNC, make, _plus_one([]), max_failures=3)
    assert [f(1), f(1)] == [2, 20]  # failure, success: the count restarts
    assert [f(1), f(1), f(1)] == [2, 2, 2]  # three in a row
    assert guard.disabled and guard.total == 4
    assert f(1) == 2 and len(wrapper_calls) == 5  # switched off: the wrapper is not called any more


def test_the_installed_callable_looks_like_the_original_to_introspection():
    def original(x: int) -> int:
        return x

    def make(inner):
        def wrapper(x):
            return inner(x)
        return wrapper
    f, _ = build("wrap", shell.SYNC, make, original)
    assert not inspect.iscoroutinefunction(f) and not inspect.isgeneratorfunction(f)


# ── sync replace ───────────────────────────────────────────────────────────────────────────


def test_a_replacement_that_raises_is_answered_by_the_recorded_original_and_counted():
    calls = []

    def replacement(x):
        raise AttributeError("upstream renamed a helper")
    f, guard = build("replace", shell.SYNC, replacement, _plus_one(calls))
    assert f(1) == 2 and calls == [1] and guard.total == 1


def test_a_replacement_exception_the_original_agrees_with_propagates_uncounted():
    def original(x):
        raise ValueError("bad input")

    def replacement(x):
        raise ValueError("bad input (copy)")
    f, guard = build("replace", shell.SYNC, replacement, original)
    with pytest.raises(ValueError, match="bad input"):
        f(1)
    assert guard.total == 0 and guard.consecutive == 0


def test_a_replacement_exception_the_original_does_not_share_is_counted_and_the_originals_exception_wins():
    def original(x):
        raise KeyError("different")

    def replacement(x):
        raise TypeError("copy bug")  # a bug-class error: the default replace_fallback answers it
    f, guard = build("replace", shell.SYNC, replacement, original)
    with pytest.raises(KeyError):
        f(1)
    assert guard.total == 1


def test_a_replacement_works_normally_when_it_does_not_raise():
    f, guard = build("replace", shell.SYNC, lambda x: x * 100, _plus_one([]))
    assert f(2) == 200 and guard.total == 0


# ── async ──────────────────────────────────────────────────────────────────────────────────


def _aplus(calls):
    async def original(x):
        calls.append(x)
        return x + 1
    return original


def test_async_wrap_pre_post_and_original_failures():
    pre_calls, post_calls = [], []

    def pre(original):
        async def wrapper(x):
            raise RuntimeError("pre")
        return wrapper

    def post(original):
        async def wrapper(x):
            await original(x)
            raise RuntimeError("post")
        return wrapper
    f, guard = build("wrap", shell.ASYNC, pre, _aplus(pre_calls))
    assert inspect.iscoroutinefunction(f) and asyncio.run(f(1)) == 2 and pre_calls == [1] and guard.total == 1
    g, guard = build("wrap", shell.ASYNC, post, _aplus(post_calls))
    assert asyncio.run(g(1)) == 2 and post_calls == [1] and guard.total == 1

    async def failing(x):
        raise ValueError("the original raised")

    def passthrough(original):
        async def wrapper(x):
            return await original(x)
        return wrapper
    h, guard = build("wrap", shell.ASYNC, passthrough, failing)
    with pytest.raises(ValueError, match="the original raised"):
        asyncio.run(h(1))
    assert guard.total == 0


def test_async_cancellation_is_never_swallowed():
    async def original(x):
        await asyncio.sleep(30)

    def make(inner):
        async def wrapper(x):
            return await inner(x)
        return wrapper
    f, guard = build("wrap", shell.ASYNC, make, original)

    async def scenario():
        task = asyncio.ensure_future(f(1))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    assert guard.total == 0


def test_async_replace_falls_back_to_the_original():
    async def replacement(x):
        raise TypeError("signature changed")
    f, guard = build("replace", shell.ASYNC, replacement, _aplus([]))
    assert asyncio.run(f(1)) == 2 and guard.total == 1


def test_concurrent_async_calls_keep_separate_frames():
    async def original(x):
        await asyncio.sleep(0.01 * (3 - x))
        return x

    def make(inner):
        async def wrapper(x):
            result = await inner(x)
            if x == 2:
                raise RuntimeError("only the call with x == 2 fails after the original returned")
            return result
        return wrapper
    f, guard = build("wrap", shell.ASYNC, make, original)

    async def scenario():
        return await asyncio.gather(f(1), f(2), f(3))
    assert asyncio.run(scenario()) == [1, 2, 3] and guard.total == 1


# ── generators ─────────────────────────────────────────────────────────────────────────────


def count(n):
    yield from range(n)


def test_a_generator_wrapper_that_fails_before_the_first_item_is_answered_by_the_original_stream():
    def make(original):
        def wrapper(n):
            raise RuntimeError("pre")
            yield  # pragma: no cover -- makes it a generator function
        return wrapper
    f, guard = build("wrap", shell.GEN, make, count)
    assert inspect.isgeneratorfunction(f) and list(f(3)) == [0, 1, 2] and guard.total == 1


def test_a_generator_wrapper_that_fails_mid_stream_propagates_and_counts():
    def make(original):
        def wrapper(n):
            yield 0
            raise RuntimeError("mid-stream bug")
        return wrapper
    f, guard = build("wrap", shell.GEN, make, count)
    stream = f(3)
    assert next(stream) == 0
    with pytest.raises(RuntimeError, match="mid-stream"):
        next(stream)
    assert guard.total == 1


def test_an_exception_from_the_original_stream_propagates_uncounted():
    def original(n):
        yield 0
        raise ValueError("original boom")

    def make(inner):
        def wrapper(n):
            for item in inner(n):
                yield item * 10
        return wrapper
    f, guard = build("wrap", shell.GEN, make, original)
    stream = f(3)
    assert next(stream) == 0
    with pytest.raises(ValueError, match="original boom"):
        next(stream)
    assert guard.total == 0


def test_a_plain_function_wrapper_returning_a_stream_works_and_close_is_forwarded():
    closed = []

    def original(n):
        try:
            yield from range(n)
        finally:
            closed.append(True)

    def make(inner):
        def wrapper(n):
            return inner(n)
        return wrapper
    f, guard = build("wrap", shell.GEN, make, original)
    stream = f(5)
    assert next(stream) == 0
    stream.close()
    assert closed == [True] and guard.total == 0


def test_a_value_sent_into_a_generator_wrapper_reaches_its_stream():
    def original(n):
        got = yield "ready"
        yield f"got {got}"

    def make(inner):
        def wrapper(n):
            return (yield from inner(n))
        return wrapper
    f, _ = build("wrap", shell.GEN, make, original)
    stream = f(0)
    assert next(stream) == "ready" and stream.send("hello") == "got hello"


def test_a_generator_replacement_that_fails_before_the_first_item_is_answered_by_the_original():
    def replacement(n):
        raise AttributeError("renamed")
        yield  # pragma: no cover
    f, guard = build("replace", shell.GEN, replacement, count)
    assert list(f(2)) == [0, 1] and guard.total == 1


# ── async generators ──────────────────────────────────────────────────────────────────────


async def acount(n):
    for i in range(n):
        yield i


async def _collect(stream):
    return [item async for item in stream]


def test_an_async_generator_wrapper_pre_and_mid_stream_failures():
    def pre(original):
        async def wrapper(n):
            raise RuntimeError("pre")
            yield  # pragma: no cover
        return wrapper

    def mid(original):
        async def wrapper(n):
            yield 0
            raise RuntimeError("mid")
        return wrapper
    f, guard = build("wrap", shell.AGEN, pre, acount)
    assert inspect.isasyncgenfunction(f) and asyncio.run(_collect(f(3))) == [0, 1, 2] and guard.total == 1
    g, guard = build("wrap", shell.AGEN, mid, acount)
    with pytest.raises(RuntimeError, match="mid"):
        asyncio.run(_collect(g(3)))
    assert guard.total == 1


def test_an_exception_from_the_original_async_stream_propagates_uncounted():
    async def original(n):
        yield 0
        raise ValueError("original boom")

    def make(inner):
        async def wrapper(n):
            async for item in inner(n):
                yield item
        return wrapper
    f, guard = build("wrap", shell.AGEN, make, original)
    with pytest.raises(ValueError, match="original boom"):
        asyncio.run(_collect(f(3)))
    assert guard.total == 0


def test_an_async_generator_replacement_falls_back_to_the_original_stream():
    async def replacement(n):
        raise TypeError("signature changed")
        yield  # pragma: no cover
    f, guard = build("replace", shell.AGEN, replacement, acount)
    assert asyncio.run(_collect(f(2))) == [0, 1] and guard.total == 1


# ── through the registry ───────────────────────────────────────────────────────────────────

OWNER = "home-a"


def _install(spec):
    key = cp.stage(spec, OWNER, granted=True, declared=True)
    cp.activate(key, OWNER)
    return key


def _spec(target, name, fn, *, kind="wrap", sha=GREET_SHA, attr="greet", propagates=()):
    return cp.make_spec(plugin_id="demo", name=name, target=f"{target.name}:{attr}", kind=kind, fn=fn,
                        expect_ast_sha=sha, propagates=propagates)


def _wait_for(predicate, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not predicate():
        time.sleep(0.01)
    return predicate()


def test_a_wrapper_raising_at_call_time_never_breaks_the_call_and_is_switched_off_after_max_failures(target):
    original = target.mod.greet

    def buggy(orig):
        def greet(name):
            raise RuntimeError("patch bug")
        return greet
    key = _install(_spec(target, "buggy", buggy))
    assert [target.mod.greet("x") for _ in range(3)] == ["hello x"] * 3  # every call answered by the original
    assert _wait_for(lambda: cp.records()[0].status == cp.DISABLED)
    record = cp.records()[0]
    assert "switched off after 3 failures in a row" in record.detail and cp.failure_stats()[key][0] == 3
    assert target.mod.greet is original  # the chain was rebuilt without it: the original is restored
    assert target.user.greet is original


def test_one_failing_patch_does_not_take_the_other_patches_of_the_chain_with_it(target):
    def buggy(orig):
        def greet(name):
            raise RuntimeError("patch bug")
        return greet

    def shout(orig):
        def greet(name):
            return orig(name).upper()
        return greet
    _install(_spec(target, "shout", shout))
    _install(_spec(target, "buggy", buggy))
    assert target.mod.greet("x") == "HELLO X"  # buggy (outer) failed and was skipped; shout still applies


def test_an_exception_of_the_original_propagates_through_an_installed_patch(tmp_path, monkeypatch):
    import importlib
    import sys

    name = "cps_" + tmp_path.name[-8:].replace("-", "_").replace(".", "_")
    (tmp_path / f"{name}.py").write_text((FIXTURES / "cpdemo_stream.py").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module(name)

    def wrap(original):
        def fails(x):
            return original(x)
        return fails
    spec = cp.make_spec(plugin_id="demo", name="w", target=f"{name}:fails", kind="wrap", fn=wrap,
                        expect_ast_sha=core_patch_sha(f"{name}:fails", tmp_path))
    _install(spec)
    try:
        for _ in range(5):
            with pytest.raises(ValueError, match="original raised for 7"):
                module.fails(7)
        assert cp.records()[0].status == cp.ACTIVE  # an exception of the original is not a patch failure
        assert cp.failure_stats()["demo:w"][0] == 0
    finally:
        sys.modules.pop(name, None)


def test_the_switch_off_is_written_to_the_events_file(target):
    from hermes_cli import core_patches_policy as policy

    def buggy(orig):
        def greet(name):
            raise RuntimeError("patch bug")
        return greet
    _install(_spec(target, "buggy", buggy))
    for _ in range(3):
        target.mod.greet("x")
    assert _wait_for(lambda: cp.records()[0].status == cp.DISABLED)
    lines = (policy.state_dir() / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert any('"event": "auto-disabled"' in line and "demo:buggy" in line for line in lines)
