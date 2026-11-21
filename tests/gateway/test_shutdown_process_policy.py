"""The shutdown process policy seam: default stays upstream, a registered policy adopts.

Upstream contract: the gateway's stop kills every running tool subprocess,
``persist_on_release`` jobs included (``kill_all(source="gateway_shutdown")``,
#41225/#46778 — a dead gateway must not leave unsupervised PPID=1 orphans).
A plugin-registered policy (``keep-processes``) asks the crash contract
instead: leave persisted host processes running — they are checkpointed and
the next gateway re-adopts them (``recover_from_checkpoint``) into the same
session. Without a policy the sweep must behave exactly as before.
"""

import pytest

from gateway import shutdown_process_policy
from tools.process_registry import ProcessRegistry, ProcessSession


def _make_session(sid, persist=False, exited=False, pid_scope="host") -> ProcessSession:
    s = ProcessSession(id=sid, command="sleep 60", task_id="t1", started_at=0.0)
    s.persist_on_release = persist
    s.exited = exited
    s.pid_scope = pid_scope
    return s


@pytest.fixture(autouse=True)
def _no_policies_left_behind():
    """Tests must not leak a policy into other tests' shutdown sweeps."""
    shutdown_process_policy._policies.clear()
    yield
    shutdown_process_policy._policies.clear()


# ----- adoptable_keep_ids (the module contract) -----

def test_no_policy_keeps_nothing():
    registry = ProcessRegistry()
    registry._running["proc_persisted"] = _make_session("proc_persisted", persist=True)
    assert shutdown_process_policy.adoptable_keep_ids(registry) == frozenset()


def test_a_policy_asking_to_keep_gets_the_persisted_host_sessions():
    registry = ProcessRegistry()
    registry._running["proc_volatile"] = _make_session("proc_volatile")
    registry._running["proc_persisted"] = _make_session("proc_persisted", persist=True)
    registry._running["proc_done"] = _make_session("proc_done", persist=True, exited=True)
    registry._running["proc_sandbox"] = _make_session("proc_sandbox", persist=True, pid_scope="sandbox")
    shutdown_process_policy.register_policy("demo:keep", lambda: True)
    assert shutdown_process_policy.adoptable_keep_ids(registry) == frozenset({"proc_persisted"})


def test_a_policy_saying_no_keeps_nothing():
    registry = ProcessRegistry()
    registry._running["proc_persisted"] = _make_session("proc_persisted", persist=True)
    shutdown_process_policy.register_policy("demo:decline", lambda: False)
    assert shutdown_process_policy.adoptable_keep_ids(registry) == frozenset()


def test_a_raising_policy_fails_closed_to_the_kill():
    """A broken policy must not accidentally keep processes: default = upstream kill."""
    registry = ProcessRegistry()
    registry._running["proc_persisted"] = _make_session("proc_persisted", persist=True)
    def boom():
        raise RuntimeError("x")
    shutdown_process_policy.register_policy("demo:boom", boom)
    assert shutdown_process_policy.adoptable_keep_ids(registry) == frozenset()


def test_register_policy_release_removes_it():
    registry = ProcessRegistry()
    registry._running["proc_persisted"] = _make_session("proc_persisted", persist=True)
    release = shutdown_process_policy.register_policy("demo:keep", lambda: True)
    release()
    assert shutdown_process_policy.adoptable_keep_ids(registry) == frozenset()


# ----- the one call site in the upstream shutdown sweep -----

@pytest.fixture()
def registry_and_kill_calls(monkeypatch):
    """Swap in a registry whose kill_all records its kwargs; stub the other teardown steps."""
    import tools.process_registry as _pr
    registry = ProcessRegistry()
    calls: list[dict] = []

    def fake_kill_all(task_id=None, **kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(_pr, "process_registry", registry)
    monkeypatch.setattr(registry, "kill_all", fake_kill_all)
    monkeypatch.setattr("cron.scheduler.mark_running_jobs_interrupted", lambda *a, **k: [])
    monkeypatch.setattr("tools.async_delegation.interrupt_all", lambda *a, **k: 0)
    monkeypatch.setattr("tools.terminal_tool_lifecycle.cleanup_all_environments", lambda: None)
    monkeypatch.setattr("tools.browser_session_lifecycle.cleanup_all_browsers", lambda: None)
    return registry, calls


def _run_sweep():
    from gateway.run_shutdown import GatewayShutdownMixin
    GatewayShutdownMixin._stop_kill_tool_subprocesses("tests")


def test_no_policy_the_sweep_kills_as_upstream(registry_and_kill_calls):
    """Neutrality: without a plugin the sweep is byte-identical to upstream —
    persist_on_release jobs included (the footprint pin for upstream-gate)."""
    registry, calls = registry_and_kill_calls
    registry._running["proc_persisted"] = _make_session("proc_persisted", persist=True)
    _run_sweep()
    assert calls, "the sweep must still call kill_all(source=gateway_shutdown)"
    for kwargs in calls:
        assert kwargs.get("source") == "gateway_shutdown"
        assert kwargs.get("exclude_ids", frozenset()) == frozenset()


def test_a_raising_policy_the_sweep_still_kills_everything(registry_and_kill_calls):
    registry, calls = registry_and_kill_calls
    registry._running["proc_persisted"] = _make_session("proc_persisted", persist=True)
    def boom():
        raise RuntimeError("x")
    shutdown_process_policy.register_policy("demo:boom", boom)
    _run_sweep()
    for kwargs in calls:
        assert kwargs.get("exclude_ids", frozenset()) == frozenset()


def test_a_keep_policy_the_sweep_excludes_the_persisted_sessions(registry_and_kill_calls):
    registry, calls = registry_and_kill_calls
    registry._running["proc_volatile"] = _make_session("proc_volatile")
    registry._running["proc_persisted"] = _make_session("proc_persisted", persist=True)
    shutdown_process_policy.register_policy("demo:keep", lambda: True)
    _run_sweep()
    assert calls, "the sweep must still run: non-persisted processes are still killed"
    for kwargs in calls:
        assert kwargs.get("source") == "gateway_shutdown"
        assert kwargs.get("exclude_ids", frozenset()) == frozenset({"proc_persisted"})


# ----- the plugin ctx path (mirrors test_busy_report's probe registration) -----

def test_a_plugin_registers_the_policy_through_its_context_and_unload_removes_it():
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest

    registry = ProcessRegistry()
    registry._running["proc_persisted"] = _make_session("proc_persisted", persist=True)
    manager = PluginManager()
    ctx = PluginContext(PluginManifest(name="keep-demo"), manager)
    handle = ctx.register_shutdown_process_policy("keep", lambda: True)
    assert shutdown_process_policy.policy_keys() == ["keep-demo:keep"]
    assert shutdown_process_policy.adoptable_keep_ids(registry) == frozenset({"proc_persisted"})
    handle.dispose()
    assert shutdown_process_policy.policy_keys() == []
    assert shutdown_process_policy.adoptable_keep_ids(registry) == frozenset()
