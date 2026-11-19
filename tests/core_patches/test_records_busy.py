"""records() keeps listing the registry while another thread holds the registry lock (hermes_cli/core_patches.py
records, _snapshot_or_busy): the status report is never blanked by a busy registry."""

from __future__ import annotations

import threading
import time

from hermes_cli import core_patches as cp
from tests.core_patches.conftest import GREET_SHA

OWNER = "home-a"


def _up(original):
    def greet(name):
        return original(name).upper()
    return greet


def test_records_still_lists_the_registered_patches_while_another_thread_holds_the_registry_lock(target, no_discovery):
    spec = cp.make_spec(plugin_id="demo", name="up", target=f"{target.name}:greet", kind="wrap", fn=_up,
                        expect_ast_sha=GREET_SHA)
    key = cp.stage(spec, OWNER, granted=True, declared=True)
    cp.activate(key, OWNER)
    holding, release = threading.Event(), threading.Event()

    def hold():
        with cp._LOCK:
            holding.set()
            release.wait(10)
    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    try:
        assert holding.wait(5)
        started = time.monotonic()
        got = cp.records()
        assert time.monotonic() - started < 3.0
        assert [r.spec.key for r in got] == [key]
    finally:
        release.set()
        holder.join(5.0)
