"""Event-loop stall monitor: say what blocked the gateway loop, while it is still blocked.

A coroutine on the gateway loop sleeps ``TICK_S`` and stamps a heartbeat; a daemon watchdog thread
(independent of the loop) polls that stamp. When the stamp is older than ``gateway.loop_lag_warn_s``
(default 5 s, 0 disables) the thread logs a WARNING with the formatted stack of the thread running the
loop, captured from ``sys._current_frames()`` while it is still blocked (at most one dump per
``DUMP_MIN_INTERVAL_S``). When the loop wakes, the coroutine logs how long it was late.

Healthy cost: one 1 s sleep on the loop plus a clock comparison on the thread. Nothing here raises
into the gateway; cancelling the coroutine stops the thread.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback
from typing import Callable, Optional

logger = logging.getLogger("gateway.loop_lag")

DEFAULT_WARN_S = 5.0
TICK_S = 1.0
DUMP_MIN_INTERVAL_S = 60.0
_MAX_STACK_CHARS = 8000


def read_warn_s() -> float:
    """``gateway.loop_lag_warn_s`` from config.yaml (seconds; <= 0 disables); the default on any problem."""
    try:
        from hermes_cli.config import read_raw_config
        raw = (read_raw_config().get("gateway") or {}).get("loop_lag_warn_s", DEFAULT_WARN_S)
        value = float(raw)
        return value if value == value else DEFAULT_WARN_S  # NaN guard
    except Exception:
        return DEFAULT_WARN_S


class StallWatchdog:
    """The loop-independent half: ``beat()`` from the loop, ``check()`` from the watchdog thread.

    ``clock`` and ``frames`` are injectable so the logic is testable without a real stall."""

    def __init__(self, loop_thread_id: int, warn_s: float, *, clock: Callable[[], float] = time.monotonic,
                 frames: Callable[[], dict] = sys._current_frames, emit: Optional[Callable[[str], None]] = None,
                 min_dump_interval_s: float = DUMP_MIN_INTERVAL_S):
        self._thread_id = loop_thread_id
        self._warn_s = warn_s
        self._clock = clock
        self._frames = frames
        self._emit = emit or (lambda text: logger.warning("%s", text))
        self._min_interval = min_dump_interval_s
        self._beat = clock()
        self._last_dump: Optional[float] = None
        self._stop = threading.Event()

    def beat(self) -> None:
        self._beat = self._clock()

    def check(self) -> Optional[str]:
        """Dump the loop thread's stack when the heartbeat is stale and a dump is due. Returns the text
        emitted, else None. Never raises."""
        try:
            now = self._clock()
            stalled_s = now - self._beat
            if stalled_s < self._warn_s:
                return None
            if self._last_dump is not None and now - self._last_dump < self._min_interval:
                return None
            self._last_dump = now
            frame = self._frames().get(self._thread_id)
            stack = ("".join(traceback.format_stack(frame)) if frame is not None
                     else "(loop thread no longer exists)")
            if len(stack) > _MAX_STACK_CHARS:
                stack = "...\n" + stack[-_MAX_STACK_CHARS:]
            text = (f"Event loop blocked for {stalled_s:.1f}s (threshold {self._warn_s:g}s); "
                    f"stack of the loop thread right now:\n{stack}")
            self._emit(text)
            return text
        except Exception:
            return None

    def run(self, poll_s: float) -> None:
        """Thread body: poll until stopped."""
        while not self._stop.wait(poll_s):
            self.check()

    def stop(self) -> None:
        self._stop.set()


async def loop_lag_monitor(warn_s: Optional[float] = None, *, tick_s: float = TICK_S) -> None:
    """Run on the gateway loop until cancelled. Returns at once when disabled (``warn_s <= 0``)."""
    try:
        warn = read_warn_s() if warn_s is None else float(warn_s)
        if warn <= 0:
            return
        watchdog = StallWatchdog(threading.get_ident(), warn)
        threading.Thread(target=watchdog.run, args=(max(0.25, min(1.0, warn / 4.0)),),
                         name="loop-lag-watchdog", daemon=True).start()
    except Exception:
        logger.debug("loop lag monitor failed to start", exc_info=True)
        return
    try:
        while True:
            started = time.monotonic()
            await asyncio.sleep(tick_s)
            watchdog.beat()
            late = time.monotonic() - started - tick_s
            if late >= warn:
                logger.warning("Event loop was late by %.1fs (a %.1fs tick woke after %.1fs)",
                               late, tick_s, late + tick_s)
    finally:
        watchdog.stop()
