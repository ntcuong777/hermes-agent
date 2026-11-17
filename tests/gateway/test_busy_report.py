"""gateway/busy_report.py: what is running, by kind, with counts and ages; probes; thread-safe collection."""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from gateway import busy_report


class FakeRunner:
    def __init__(self, units=()):
        self.units = list(units)

    def _describe_active_work(self):
        return list(self.units)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from tools import approval, clarify_gateway, code_kernel
    from tools.process_registry import process_registry

    monkeypatch.setattr(process_registry, "list_sessions", lambda *a, **k: [])
    monkeypatch.setattr(approval, "_gateway_queues", {})
    monkeypatch.setattr(clarify_gateway, "_entries", {})
    monkeypatch.setattr(code_kernel, "_KERNELS", {})
    monkeypatch.setattr(code_kernel._REGISTRY, "kernels", code_kernel._KERNELS)
    monkeypatch.setattr(busy_report, "_probes", {})
    monkeypatch.setattr(busy_report, "_first_seen", {})
    monkeypatch.setattr(busy_report, "_runner_ref", None)
    monkeypatch.setattr(busy_report, "_loop", None)


def test_an_empty_gateway_is_idle():
    report = busy_report.collect(FakeRunner())
    assert (report["busy"], report["blocking"], report["uncertain"]) == (False, 0, False)
    assert set(busy_report.KINDS) <= set(report["kinds"])
    assert busy_report.format_report(report).startswith("idle")


def test_core_units_are_sorted_into_kinds_with_ages():
    units = [
        {"kind": "chat", "session": "agent:main:tg:1", "elapsed_s": 42.0, "idle_s": 3, "current_tool": "terminal"},
        {"kind": "cron", "job_id": "digest", "elapsed_s": 90.0, "restart_safe": False, "wedged": False},
        {"kind": "delegation", "delegation_id": "d1", "goal": "refactor", "elapsed_s": 600.0},
        {"kind": "notice", "type": "async_delegation", "id": "d0"},
    ]
    report = busy_report.collect(FakeRunner(units))
    kinds = report["kinds"]
    assert kinds["turns"]["count"] == kinds["cron"]["count"] == 1
    assert kinds["delegations"]["count"] == 2
    assert kinds["turns"]["items"][0]["tool"] == "terminal"
    assert kinds["delegations"]["oldest_age_s"] == 600.0
    assert report["busy"] and report["blocking"] == 4


def test_wedged_turns_and_restart_safe_cron_are_listed_but_do_not_block(monkeypatch):
    monkeypatch.setenv("HERMES_AGENT_TIMEOUT", "100")
    units = [{"kind": "chat", "session": "s", "elapsed_s": 5000, "idle_s": 4000},
             {"kind": "cron", "job_id": "j", "elapsed_s": 5, "restart_safe": True},
             {"kind": "cron", "job_id": "w", "elapsed_s": 5, "wedged": True}]
    report = busy_report.collect(FakeRunner(units))
    assert report["kinds"]["turns"]["count"] == 1 and report["kinds"]["turns"]["blocking"] == 0
    assert report["kinds"]["cron"]["count"] == 2 and report["kinds"]["cron"]["blocking"] == 0
    assert report["busy"] is False


def test_a_silent_background_process_blocks_although_the_core_wait_ignores_it(monkeypatch):
    from tools.process_registry import process_registry

    monkeypatch.setattr(process_registry, "list_sessions", lambda *a, **k: [
        {"session_id": "proc_1", "command": "sleep 3000", "pid": 77, "uptime_seconds": 120, "status": "running"},
        {"session_id": "proc_2", "command": "true", "pid": 78, "uptime_seconds": 1, "status": "exited"}])
    report = busy_report.collect(FakeRunner())
    items = report["kinds"]["processes"]["items"]
    assert [i["id"] for i in items] == ["proc_1"] and items[0]["age_s"] == 120
    assert "watched" not in items[0]
    assert report["busy"]


def test_a_watched_process_that_exited_but_still_owes_its_notice_blocks(monkeypatch):
    units = [{"kind": "process", "process_id": "proc_9", "command": "make", "exited": True, "elapsed_s": 30.0}]
    report = busy_report.collect(FakeRunner(units))
    assert report["kinds"]["processes"]["items"][0]["exited"] is True and report["busy"]


def test_kernels_block_only_while_a_cell_runs():
    from tools import code_kernel

    now = time.monotonic()
    code_kernel._KERNELS[("a",)] = SimpleNamespace(proc=SimpleNamespace(pid=10), last_used=now - 50, attached=0, execution_count=3)
    code_kernel._KERNELS[("b",)] = SimpleNamespace(proc=SimpleNamespace(pid=11), last_used=now - 2, attached=1, execution_count=1)
    kernels = busy_report.collect(FakeRunner())["kinds"]["kernels"]
    assert kernels["count"] == 2 and kernels["blocking"] == 1
    assert [i["running"] for i in kernels["items"]] == [False, True]


def test_pending_approvals_and_clarifies_are_blockers_with_a_first_seen_age(monkeypatch):
    from tools import approval, clarify_gateway

    approval._gateway_queues["sess"] = [SimpleNamespace(data={"request_id": "r1", "command": "rm -rf x", "description": "delete"})]
    clarify_gateway.register("c1", "sess", "which one?", None)
    first = busy_report.collect(FakeRunner(), now=1000.0)
    again = busy_report.collect(FakeRunner(), now=1030.0)
    assert first["kinds"]["approvals"]["blocking"] == first["kinds"]["clarifies"]["blocking"] == 1
    assert first["kinds"]["approvals"]["items"][0]["age_s"] == 0
    assert again["kinds"]["approvals"]["items"][0]["age_s"] == 30
    approval._gateway_queues.clear()
    gone = busy_report.collect(FakeRunner(), now=1060.0)
    assert gone["kinds"]["approvals"]["count"] == 0 and ("approvals", "r1") not in busy_report._first_seen


def test_probes_add_sections_and_are_removed_by_their_release():
    release = busy_report.register_probe("demo:jobs", lambda: [{"id": "j1", "label": "nightly", "age_s": 9}, "bare"])
    busy_report.register_probe("demo:count", lambda: 2)
    busy_report.register_probe("demo:flag", lambda: False)
    report = busy_report.collect(FakeRunner())
    assert [i["id"] for i in report["kinds"]["demo:jobs"]["items"]] == ["j1", "bare"]
    assert report["kinds"]["demo:count"]["count"] == 2 and report["kinds"]["demo:flag"]["count"] == 0
    release()
    assert "demo:jobs" not in busy_report.collect(FakeRunner())["kinds"]
    assert busy_report.probe_names() == ["demo:count", "demo:flag"]


def test_a_failing_probe_makes_the_report_uncertain_and_busy_without_hiding_the_rest():
    def boom():
        raise RuntimeError("no")
    busy_report.register_probe("demo:boom", boom)
    report = busy_report.collect(FakeRunner([{"kind": "delegation", "delegation_id": "d", "goal": "g", "elapsed_s": 1}]))
    assert report["uncertain"] and report["busy"]
    assert "demo:boom" in report["errors"] and report["kinds"]["delegations"]["count"] == 1
    assert busy_report.format_report(report).endswith("RuntimeError: no")


def test_register_probe_rejects_a_blank_name_or_non_callable():
    with pytest.raises(ValueError):
        busy_report.register_probe(" ", lambda: 0)
    with pytest.raises(ValueError):
        busy_report.register_probe("x", None)


def test_log_view_has_ids_and_ages_but_no_labels():
    report = busy_report.collect(FakeRunner([{"kind": "delegation", "delegation_id": "d1", "goal": "SECRET GOAL", "elapsed_s": 75}]))
    view = busy_report.log_view(report)
    assert "d1@1m15s" in view and "SECRET" not in view


def test_collect_threadsafe_without_a_runner_is_idle():
    report = busy_report.collect_threadsafe()
    assert (report["busy"], report["uncertain"], report["no_runner"]) == (False, False, True)


def test_collect_threadsafe_runs_on_the_loop_thread_and_times_out_as_uncertain():
    seen = {}

    class Runner(FakeRunner):
        def _describe_active_work(self):
            seen["thread"] = threading.current_thread().name
            return []

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="loop-thread", daemon=True)
    thread.start()
    runner = Runner()
    try:
        loop.call_soon_threadsafe(busy_report.bind_runner, runner)
        deadline = time.time() + 2
        while busy_report.current_runner() is None and time.time() < deadline:
            time.sleep(0.01)
        assert busy_report.collect_threadsafe(timeout=2)["busy"] is False
        assert seen["thread"] == "loop-thread"
        loop.call_soon_threadsafe(lambda: time.sleep(0.5))  # a stalled loop
        stalled = busy_report.collect_threadsafe(timeout=0.1)
        assert stalled["busy"] and stalled["uncertain"] and "loop" in stalled["errors"]
        busy_report.unbind_runner(runner)
        assert busy_report.current_runner() is None
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)


def test_a_plugin_registers_a_busy_probe_through_its_context_and_unload_removes_it():
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest

    manager = PluginManager()
    ctx = PluginContext(PluginManifest(name="demo"), manager)
    handle = ctx.register_busy_probe("jobs", lambda: [{"id": "j1"}])
    assert busy_report.probe_names() == ["demo:jobs"]
    assert busy_report.collect(FakeRunner())["kinds"]["demo:jobs"]["count"] == 1
    handle.dispose()
    assert busy_report.probe_names() == []


def test_the_runner_binds_and_unbinds_with_its_plugin_message_injector():
    from gateway.run_plugin_injection import GatewayPluginInjectionMixin

    class Host(GatewayPluginInjectionMixin):
        def _schedule_plugin_message_injection(self, **kwargs):
            return False

    async def go():
        host = Host()
        host._install_plugin_message_injector()
        bound = busy_report.current_runner() is host and busy_report.current_loop() is asyncio.get_running_loop()
        host._clear_plugin_message_injector()
        return bound, busy_report.current_runner()

    assert asyncio.run(go()) == (True, None)
