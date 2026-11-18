"""The session-lane seam: with no plugin it is upstream's behaviour, and it fails open.

Two layers: the dispatchers against a stubbed plugin API (every failure mode), and the three hooks through
a real ``PluginManager`` (registered names, argument narrowing, async callbacks, a refusal carried as a
returned ``RuntimeStoreError``).
"""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from gateway import session_lane_seam as seam
from hermes_state_runtime import RuntimeStoreError


@pytest.fixture(autouse=True)
def _fresh_warnings(monkeypatch):
    monkeypatch.setattr(seam, "_WARNED", set())


def _stub(monkeypatch, *, wanted=True, sync=None, asyn=None):
    """Replace the plugin API the seam imports lazily: has_hook / invoke_hook / ainvoke_hook."""
    from hermes_cli import plugins

    monkeypatch.setattr(plugins, "has_hook", lambda name: wanted)
    monkeypatch.setattr(plugins, "invoke_hook", sync or (lambda hook, **kw: []))

    async def default_async(hook, **kw):
        return []
    monkeypatch.setattr(plugins, "ainvoke_hook", asyn or default_async)


async def _call_value():
    return "upstream"


# ── the registered names ─────────────────────────────────────────────────────────────────────────────────


def test_the_three_hook_names_are_valid_hooks():
    from hermes_cli.plugins import VALID_HOOKS

    assert set(seam.HOOKS) <= VALID_HOOKS
    assert seam.HOOKS == ("observe_session_check", "wrap_session_call", "route_lane_operation")


# ── no plugin: upstream's behaviour ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_with_no_plugin_every_dispatcher_returns_upstreams_answer(monkeypatch):
    _stub(monkeypatch, wanted=False)
    seam.observe_check("s", "session:read")
    assert await seam.guard(object(), object(), "m", _call_value) == "upstream"
    assert await seam.submit_refused(object(), object(), {}, RuntimeStoreError("permission_denied")) is None
    assert await seam.command_refused(object(), object(), "n", "", "refused", object()) is None
    assert await seam.attach_info(object(), object()) == {}
    info = {"model": None}
    seam.complete_info(object(), object(), info, "s", None)
    seam.record_handoff(object(), "target", {})
    assert info == {"model": None}
    with pytest.raises(RuntimeStoreError) as refused:
        await seam.nonlocal_command(object(), object(), "n", "", None, "session:read", "read")
    assert refused.value.reason == "permission_denied"
    with pytest.raises(RuntimeStoreError) as refused:
        await seam.rollback_policy(object(), object(), "list")
    assert refused.value.reason == "permission_denied"


# ── fail open: the plugin API itself breaks ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_broken_plugin_api_is_upstreams_behaviour(monkeypatch):
    from hermes_cli import plugins

    def boom(*args, **kwargs):
        raise RuntimeError("plugin system exploded")

    async def aboom(*args, **kwargs):
        raise RuntimeError("plugin system exploded")
    monkeypatch.setattr(plugins, "has_hook", lambda name: True)
    monkeypatch.setattr(plugins, "invoke_hook", boom)
    monkeypatch.setattr(plugins, "ainvoke_hook", aboom)

    seam.observe_check("s", "session:read")
    assert await seam.guard(object(), object(), "m", _call_value) == "upstream"
    assert await seam.submit_refused(object(), object(), {}, RuntimeStoreError("x")) is None
    assert await seam.attach_info(object(), object()) == {}
    seam.complete_info(object(), object(), {}, "s", None)
    with pytest.raises(RuntimeStoreError) as refused:
        await seam.nonlocal_command(object(), object(), "n", "", None, "session:read", "read")
    assert refused.value.reason == "permission_denied"


def test_has_hook_raising_is_no_plugin(monkeypatch):
    from hermes_cli import plugins

    def boom(name):
        raise RuntimeError("no registry")
    monkeypatch.setattr(plugins, "has_hook", boom)
    assert seam._wanted("route_lane_operation") is False


# ── guard: wrappers ──────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_wrapper_can_translate_the_call(monkeypatch):
    def wrapper(call):
        async def wrapped():
            return (await call()).upper()
        return wrapped
    _stub(monkeypatch, sync=lambda hook, **kw: [wrapper])
    assert await seam.guard(object(), object(), "m", _call_value) == "UPSTREAM"


@pytest.mark.asyncio
async def test_wrappers_stack_in_registration_order_last_outermost(monkeypatch):
    def tag(label):
        def wrapper(call):
            async def wrapped():
                return f"{label}({await call()})"
            return wrapped
        return wrapper
    _stub(monkeypatch, sync=lambda hook, **kw: [tag("a"), tag("b")])
    assert await seam.guard(object(), object(), "m", _call_value) == "b(a(upstream))"


@pytest.mark.asyncio
async def test_a_wrapper_can_rerun_the_call_and_a_deliberate_refusal_is_honoured(monkeypatch):
    runs = []

    async def call():
        runs.append(1)
        raise RuntimeStoreError("permission_denied")

    def wrapper(inner):
        async def wrapped():
            try:
                return await inner()
            except RuntimeStoreError as exc:
                raise RuntimeStoreError("lane_owned") from exc
        return wrapped
    _stub(monkeypatch, sync=lambda hook, **kw: [wrapper])
    with pytest.raises(RuntimeStoreError) as refused:
        await seam.guard(object(), object(), "m", call)
    assert refused.value.reason == "lane_owned" and runs == [1]


@pytest.mark.asyncio
async def test_a_wrapper_that_cannot_be_built_is_skipped(monkeypatch):
    def broken(call):
        raise RuntimeError("cannot build")
    _stub(monkeypatch, sync=lambda hook, **kw: [broken])
    assert await seam.guard(object(), object(), "m", _call_value) == "upstream"


@pytest.mark.asyncio
async def test_a_wrapper_that_crashes_before_the_call_runs_the_call_plain(monkeypatch):
    runs = []

    async def call():
        runs.append(1)
        return "upstream"

    def wrapper(inner):
        async def wrapped():
            raise ValueError("plugin bug before the call")
        return wrapped
    _stub(monkeypatch, sync=lambda hook, **kw: [wrapper])
    assert await seam.guard(object(), object(), "m", call) == "upstream"
    assert runs == [1]


@pytest.mark.asyncio
async def test_a_wrapper_that_crashes_after_the_call_ran_returns_the_calls_first_outcome(monkeypatch):
    runs = []

    async def call():
        runs.append(1)
        return "upstream"

    def wrapper(inner):
        async def wrapped():
            await inner()
            raise ValueError("plugin bug after the call")
        return wrapped
    _stub(monkeypatch, sync=lambda hook, **kw: [wrapper])
    assert await seam.guard(object(), object(), "m", call) == "upstream"
    assert runs == [1]  # the call is never run twice because a plugin failed


@pytest.mark.asyncio
async def test_the_calls_own_error_passes_through_unchanged(monkeypatch):
    async def call():
        raise KeyError("handler bug")

    def wrapper(inner):
        async def wrapped():
            return await inner()
        return wrapped
    _stub(monkeypatch, sync=lambda hook, **kw: [wrapper])
    with pytest.raises(KeyError):
        await seam.guard(object(), object(), "m", call)


@pytest.mark.asyncio
async def test_cancellation_is_never_swallowed(monkeypatch):
    async def call():
        raise asyncio.CancelledError()

    def wrapper(inner):
        async def wrapped():
            return await inner()
        return wrapped
    _stub(monkeypatch, sync=lambda hook, **kw: [wrapper])
    with pytest.raises(asyncio.CancelledError):
        await seam.guard(object(), object(), "m", call)


# ── route: results ───────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_dict_answer_reaches_the_call_site_and_a_malformed_one_does_not(monkeypatch):
    async def answer(hook, **kw):
        return [{"queued": True}]
    _stub(monkeypatch, asyn=answer)
    assert await seam.submit_refused(object(), object(), {}, RuntimeStoreError("x")) == {"queued": True}

    async def malformed(hook, **kw):
        return ["not a dict", 7]
    _stub(monkeypatch, asyn=malformed)
    assert await seam.submit_refused(object(), object(), {}, RuntimeStoreError("x")) is None


@pytest.mark.asyncio
async def test_nonlocal_command_answers(monkeypatch):
    async def run_here(hook, **kw):
        return [seam.RUN_HERE]
    _stub(monkeypatch, asyn=run_here)
    assert await seam.nonlocal_command(object(), object(), "n", "", None, "session:read", "read") is None

    async def relayed(hook, **kw):
        return [{"type": "exec", "output": "sent"}]
    _stub(monkeypatch, asyn=relayed)
    assert await seam.nonlocal_command(object(), object(), "n", "", None, "session:read", "read") == {
        "type": "exec", "output": "sent"}

    async def refusing(hook, **kw):
        return [RuntimeStoreError("lane_owned")]
    _stub(monkeypatch, asyn=refusing)
    with pytest.raises(RuntimeStoreError) as refused:
        await seam.nonlocal_command(object(), object(), "n", "", None, "session:read", "read")
    assert refused.value.reason == "lane_owned"

    async def malformed(hook, **kw):
        return [42]
    _stub(monkeypatch, asyn=malformed)
    with pytest.raises(RuntimeStoreError) as refused:
        await seam.nonlocal_command(object(), object(), "n", "", None, "session:read", "read")
    assert refused.value.reason == "permission_denied"


def test_a_sync_dispatch_ignores_an_async_callbacks_coroutine(monkeypatch):
    async def coroutine():
        return {"model": "x"}
    _stub(monkeypatch, sync=lambda hook, **kw: [coroutine()])
    info = {}
    seam.complete_info(object(), object(), info, "s", None)
    assert info == {}


# ── through a real plugin manager ────────────────────────────────────────────────────────────────────────


def _manager(monkeypatch):
    from hermes_cli import plugins as plugins_mod
    from hermes_cli.plugins import PluginManager, PluginManifest
    from hermes_cli.plugins_context import PluginContext

    manager = PluginManager()
    monkeypatch.setattr(plugins_mod, "_delivery_manager", lambda: manager)
    return PluginContext(PluginManifest(name="lane-test-plugin"), manager)


@pytest.mark.asyncio
async def test_the_real_manager_runs_sync_and_async_callbacks_with_narrowed_kwargs(monkeypatch):
    ctx = _manager(monkeypatch)
    seen = []

    def observe(session_id, capability):  # narrow signature: gets only what it declares
        seen.append((session_id, capability))
    ctx.register_hook("observe_session_check", observe)

    async def route(kind, **ctx_kwargs):  # async callback awaited on the caller's loop
        return {"kind": kind, "got": sorted(ctx_kwargs)} if kind == "submit" else None
    ctx.register_hook("route_lane_operation", route)

    seam.observe_check("s1", "session:submit")
    assert seen == [("s1", "session:submit")]
    found = await seam.submit_refused("conn", "ref", {"text": "x"}, RuntimeStoreError("permission_denied"))
    assert found["kind"] == "submit"
    assert {"connection", "ref", "params", "error"} <= set(found["got"])
    assert await seam.attach_info("authority", "ref") == {}  # a kind the callback has no opinion on


@pytest.mark.asyncio
async def test_the_real_manager_isolates_a_raising_callback_and_carries_refusals_as_values(monkeypatch):
    ctx = _manager(monkeypatch)

    def raising(kind, **kw):
        raise RuntimeError("plugin bug")
    ctx.register_hook("route_lane_operation", raising)
    assert await seam.attach_info("authority", "ref") == {}  # isolated: upstream's default

    def refusing(kind, **kw):
        return RuntimeStoreError("lane_owned") if kind == "rollback_policy" else None
    ctx.register_hook("route_lane_operation", refusing)
    with pytest.raises(RuntimeStoreError) as refused:
        await seam.rollback_policy("authority", "ref", "restore")
    assert refused.value.reason == "lane_owned"


@pytest.mark.asyncio
async def test_the_real_manager_runs_a_wrap_hook(monkeypatch):
    ctx = _manager(monkeypatch)

    def wrap(connection, ref, method):
        if method != "prompt.submit":
            return None
        return lambda call: (lambda: _shout(call))

    async def _shout(call):
        return (await call()).upper()
    ctx.register_hook("wrap_session_call", wrap)
    assert await seam.guard("c", "r", "prompt.submit", _call_value) == "UPSTREAM"
    assert await seam.guard("c", "r", "session.list", _call_value) == "upstream"


def test_record_handoff_reaches_a_sync_callback_and_a_failing_one_is_isolated(monkeypatch):
    ctx = _manager(monkeypatch)
    seen = []

    def boom(kind, **kw):
        raise RuntimeError("plugin bug")
    ctx.register_hook("route_lane_operation", boom)

    def record(kind, conn=None, target=None, **kw):
        if kind == "handoff_route":
            seen.append((conn, target))
    ctx.register_hook("route_lane_operation", record)

    seam.record_handoff("conn", "row-1", {"config_json": "{}"})  # must not raise

    assert seen == [("conn", "row-1")]


# ── mutation_guard: fail closed for a lane's transcript write, whatever the plugin state ─────────────────────


class _Rows:
    def __init__(self, policy):
        self.policy = policy

    def execute(self, sql, params):
        return SimpleNamespace(fetchone=lambda: (1,) if self.policy else None)


class _Db:
    def __init__(self, policy=False):
        self.policy = policy

    @contextmanager
    def _read_ctx(self):
        yield _Rows(self.policy)

    def get_session(self, sid):
        return {"id": sid, "chat_id": "157999"}


def _authority(platform="discord", policy=False):
    from gateway.config import Platform

    source = None if platform is None else SimpleNamespace(
        platform=Platform.LOCAL if platform == "local" else Platform.DISCORD)
    return SimpleNamespace(sessions={"s": SimpleNamespace(source=source)}, db=_Db(policy))


_REF = SimpleNamespace(session_id="s")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["rewind", "compress", "reset", "branch"])
async def test_mutation_guard_refuses_a_lane_write_with_no_plugin(monkeypatch, operation):
    _stub(monkeypatch, wanted=False)
    with pytest.raises(RuntimeStoreError) as refused:
        await seam.mutation_guard(_authority(), "actor", _REF, {"operation": operation})
    assert refused.value.reason == "permission_denied"


@pytest.mark.asyncio
async def test_mutation_guard_refuses_when_a_plugin_callback_raises(monkeypatch):
    async def raising(hook, **kw):
        raise RuntimeError("plugin bug")
    _stub(monkeypatch, asyn=raising)
    with pytest.raises(RuntimeStoreError) as refused:
        await seam.mutation_guard(_authority(), "actor", _REF, {"operation": "rewind"})
    assert refused.value.reason == "permission_denied"


@pytest.mark.asyncio
async def test_mutation_guard_lets_a_session_no_lane_binds_run(monkeypatch):
    async def run_here(hook, **kw):
        assert kw["operation"] == "rewind"
        return [seam.RUN_HERE]
    _stub(monkeypatch, asyn=run_here)
    assert await seam.mutation_guard(_authority(), "actor", _REF, {"operation": "rewind"}) is None


@pytest.mark.asyncio
async def test_mutation_guard_honours_a_plugins_own_refusal(monkeypatch):
    async def refusing(hook, **kw):
        return [RuntimeStoreError("lane_owned")]
    _stub(monkeypatch, asyn=refusing)
    with pytest.raises(RuntimeStoreError) as refused:
        await seam.mutation_guard(_authority(), "actor", _REF, {"operation": "rewind"})
    assert refused.value.reason == "lane_owned"


@pytest.mark.asyncio
async def test_mutation_guard_does_not_consult_the_plugin_for_other_writes_or_local_sessions(monkeypatch):
    async def never(hook, **kw):  # a raise here would be swallowed by route(): the refusal would show instead
        raise AssertionError("the plugin must not be asked")
    _stub(monkeypatch, asyn=never)
    assert await seam.mutation_guard(_authority(), "actor", _REF, {"operation": "rename"}) is None
    assert await seam.mutation_guard(_authority("local"), "actor", _REF, {"operation": "rewind"}) is None
    assert await seam.mutation_guard(_authority(None), "actor", _REF, {"operation": "rewind"}) is None
    assert await seam.mutation_guard(_authority(policy=True), "actor", _REF, {"operation": "rewind"}) is None
    assert await seam.mutation_guard(_authority(), "actor", _REF, "not a dict") is None
