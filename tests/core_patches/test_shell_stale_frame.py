"""A frame of a finished call is closed: an original entered later from a context copied into a worker thread
is untracked, so the original is never attributed to the finished call and never run a second time
(hermes_cli/core_patches_shell.py, _frame_for)."""

from __future__ import annotations

import asyncio
import contextvars
import queue
import threading

import pytest

from hermes_cli import core_patches_shell as shell
from tests.core_patches.test_failsafe_shell import build


def _serve(jobs, orig):
    """Worker thread body: runs the original (the trampoline) once per job, then signals the job."""
    while True:
        job = jobs.get()
        if job is None:
            return
        value, done = job
        try:
            orig(value)
        finally:
            done.set()


def _serve_async_in_thread(jobs, orig):
    while True:
        job = jobs.get()
        if job is None:
            return
        value, done = job
        try:
            asyncio.run(orig(value))
        finally:
            done.set()


def test_an_original_entered_by_a_worker_after_its_call_finished_is_untracked_and_the_original_runs_once_per_call():
    calls, jobs, started = [], queue.Queue(), []

    def original(x):
        calls.append(x)
        return x

    def make(orig):
        def wrapper(x):
            if not started:
                started.append(1)
                orig("a")  # call A: the original runs inside its own frame
                ctx = contextvars.copy_context()  # the worker inherits frame A (a copied context)
                threading.Thread(target=lambda: ctx.run(_serve, jobs, orig), daemon=True).start()
                return "a-done"
            done = threading.Event()
            jobs.put(("job", done))
            done.wait(5)  # the worker has run the original for call B
            raise RuntimeError("b failed after the worker ran the original")
        return wrapper

    f, guard = build("wrap", shell.SYNC, make, original)
    try:
        assert f("call-a") == "a-done"
        with pytest.raises(RuntimeError, match="b failed"):
            f("call-b")
    finally:
        jobs.put(None)
    assert calls == ["a", "job"]  # once for A, once for B via the worker; never a third run
    assert guard.total == 1


def test_an_async_original_entered_by_a_worker_after_its_call_finished_is_untracked_and_runs_once_per_call():
    calls, jobs, started = [], queue.Queue(), []

    async def original(x):
        calls.append(x)
        return x

    def make(orig):
        async def wrapper(x):
            if not started:
                started.append(1)
                await orig("a")
                ctx = contextvars.copy_context()
                threading.Thread(target=lambda: ctx.run(_serve_async_in_thread, jobs, orig), daemon=True).start()
                return "a-done"
            done = threading.Event()
            jobs.put(("job", done))
            done.wait(5)
            raise RuntimeError("b failed after the worker ran the original")
        return wrapper

    f, guard = build("wrap", shell.ASYNC, make, original)
    try:
        assert asyncio.run(f("call-a")) == "a-done"
        with pytest.raises(RuntimeError, match="b failed"):
            asyncio.run(f("call-b"))
    finally:
        jobs.put(None)
    assert calls == ["a", "job"]
    assert guard.total == 1
