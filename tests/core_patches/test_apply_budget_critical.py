"""A hung factory of a critical patch (holding the registry lock) never blocks apply_core_patches past its
budget, reporting included (hermes_cli/core_patches.py failure_stats, _snapshot_or_busy)."""

from __future__ import annotations

import threading
import time

from hermes_cli import core_patches as cp
from hermes_cli import core_patches_policy as policy
from tests.core_patches.conftest import GREET_SHA

OWNER = "home-a"


def test_a_hung_critical_factory_does_not_block_apply_core_patches_past_the_budget(target, no_discovery, monkeypatch):
    release = threading.Event()

    def hang(original):
        release.wait(30)  # the factory runs under the registry lock (activate -> _link)
        return lambda name: original(name)
    spec = cp.make_spec(plugin_id="demo", name="hang", target=f"{target.name}:greet", kind="wrap", fn=hang,
                        expect_ast_sha=GREET_SHA, critical=True)
    key = cp.stage(spec, OWNER, granted=True, declared=True)
    holder = threading.Thread(target=cp.activate, args=(key, OWNER), daemon=True)
    monkeypatch.setattr(policy, "load_settings",
                        lambda: policy.Settings(apply_timeout_s=0.5, refuse_on_critical=False))
    try:
        holder.start()
        time.sleep(0.2)  # the activation is now inside the hung factory
        result = []
        caller = threading.Thread(target=lambda: result.append(cp.apply_core_patches("test")), daemon=True)
        caller.start()
        caller.join(4.0)
        assert not caller.is_alive()
        assert isinstance(result[0], tuple)
    finally:
        release.set()
        holder.join(5.0)
