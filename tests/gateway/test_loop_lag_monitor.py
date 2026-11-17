"""The loop stall monitor dumps the loop thread's stack while it is blocked, at most once a minute,
and costs nothing when the loop is healthy."""
import asyncio
import threading

import pytest

from gateway import loop_lag_monitor as mod
from gateway.loop_lag_monitor import StallWatchdog


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class BlockedLoopThread:
    """A real thread parked inside a recognisable function, standing in for a blocked event loop."""

    def __init__(self):
        self.release = threading.Event()
        self.parked = threading.Event()
        self.thread = threading.Thread(target=self.blocking_sqlite_call, daemon=True)

    def blocking_sqlite_call(self):
        self.parked.set()
        self.release.wait(10)

    def __enter__(self):
        self.thread.start()
        assert self.parked.wait(5)
        return self.thread.ident

    def __exit__(self, *exc):
        self.release.set()
        self.thread.join(5)


def _watchdog(thread_id, clock, emitted, **kw):
    return StallWatchdog(thread_id, 5.0, clock=clock, emit=emitted.append, **kw)


def test_healthy_loop_never_dumps():
    clock, emitted = FakeClock(), []
    with BlockedLoopThread() as tid:
        wd = _watchdog(tid, clock, emitted)
        for _ in range(100):
            clock.now += 1.0
            wd.beat()
            assert wd.check() is None
    assert emitted == []


def test_a_stalled_loop_gets_its_blocked_stack_dumped():
    clock, emitted = FakeClock(), []
    with BlockedLoopThread() as tid:
        wd = _watchdog(tid, clock, emitted)
        clock.now += 4.9
        assert wd.check() is None
        clock.now += 0.2
        text = wd.check()
    assert text is not None and emitted == [text]
    assert "blocked for 5.1s" in text and "blocking_sqlite_call" in text


def test_dumps_are_rate_limited_to_one_per_minute_then_resume():
    clock, emitted = FakeClock(), []
    with BlockedLoopThread() as tid:
        wd = _watchdog(tid, clock, emitted)
        clock.now += 6
        assert wd.check()
        clock.now += 30
        assert wd.check() is None
        clock.now += 29
        assert wd.check() is None  # 59 s since the dump
        clock.now += 2
        assert wd.check()  # 61 s: due again, same ongoing stall
    assert len(emitted) == 2


def test_a_beat_clears_the_stall():
    clock, emitted = FakeClock(), []
    with BlockedLoopThread() as tid:
        wd = _watchdog(tid, clock, emitted)
        clock.now += 20
        wd.beat()
        assert wd.check() is None
    assert emitted == []


def test_a_vanished_loop_thread_or_failing_emit_never_raises():
    clock = FakeClock()

    def boom(_):
        raise RuntimeError("log sink down")

    wd = StallWatchdog(-1, 5.0, clock=clock, emit=boom)
    clock.now += 10
    assert wd.check() is None  # emit raised: swallowed
    emitted = []
    wd = StallWatchdog(-1, 5.0, clock=clock, emit=emitted.append)
    clock.now += 10
    assert "no longer exists" in wd.check()


@pytest.mark.asyncio
async def test_disabled_returns_immediately_and_starts_no_thread():
    before = threading.active_count()
    await asyncio.wait_for(mod.loop_lag_monitor(0), 1)
    assert threading.active_count() == before


@pytest.mark.asyncio
async def test_running_monitor_stops_its_thread_on_cancel():
    task = asyncio.create_task(mod.loop_lag_monitor(5.0, tick_s=0.01))
    await asyncio.sleep(0.1)
    assert any(t.name == "loop-lag-watchdog" for t in threading.enumerate())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(50):
        if not any(t.name == "loop-lag-watchdog" and t.is_alive() for t in threading.enumerate()):
            break
        await asyncio.sleep(0.1)
    assert not any(t.name == "loop-lag-watchdog" and t.is_alive() for t in threading.enumerate())


@pytest.mark.asyncio
async def test_a_blocked_real_loop_is_dumped_by_the_watchdog_thread(caplog):
    """End to end: block the actual loop with time.sleep; the thread logs the stack mid-stall."""
    import time
    caplog.set_level("WARNING", logger="gateway.loop_lag")
    task = asyncio.create_task(mod.loop_lag_monitor(0.4, tick_s=0.05))
    await asyncio.sleep(0.2)
    time.sleep(1.2)  # blocks the loop; the watchdog thread dumps meanwhile
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    messages = [r.getMessage() for r in caplog.records]
    assert any("Event loop blocked for" in m and "time.sleep" in m for m in messages), messages
    assert any("Event loop was late by" in m for m in messages), messages


def test_warn_threshold_comes_from_gateway_config(monkeypatch):
    import hermes_cli.config as cfg
    monkeypatch.setattr(cfg, "read_raw_config", lambda: {"gateway": {"loop_lag_warn_s": 12}})
    assert mod.read_warn_s() == 12.0
    monkeypatch.setattr(cfg, "read_raw_config", lambda: {"gateway": {"loop_lag_warn_s": 0}})
    assert mod.read_warn_s() == 0.0
    monkeypatch.setattr(cfg, "read_raw_config", lambda: {})
    assert mod.read_warn_s() == mod.DEFAULT_WARN_S
    monkeypatch.setattr(cfg, "read_raw_config", lambda: {"gateway": {"loop_lag_warn_s": "junk"}})
    assert mod.read_warn_s() == mod.DEFAULT_WARN_S
