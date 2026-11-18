"""The ``session_opened`` seam kind (spec 08b stage B10): fail-open, async, answered through a real plugin manager."""

import pytest

import gateway.session_lane_seam as seam


def test_the_kind_is_declared_and_the_docstring_says_kinds_are_not_all_about_lanes():
    assert "session_opened" in seam.KINDS
    assert "Kinds are not all about lanes" in seam.__doc__


@pytest.mark.asyncio
async def test_with_no_plugin_a_session_open_is_a_no_op():
    assert await seam.session_opened(object(), object(), "key", True) is None


def _context(monkeypatch):
    from hermes_cli import plugins as plugins_mod
    from hermes_cli.plugins import PluginManager, PluginManifest
    from hermes_cli.plugins_context import PluginContext

    manager = PluginManager()
    monkeypatch.setattr(plugins_mod, "_delivery_manager", lambda: manager)
    return PluginContext(PluginManifest(name="workspace-seam-test"), manager)


@pytest.mark.asyncio
async def test_the_callback_gets_runner_event_session_key_and_is_new_by_name(monkeypatch):
    ctx = _context(monkeypatch)
    seen = []

    async def route(kind, **kwargs):
        seen.append((kind, sorted(kwargs)))
    ctx.register_hook("route_lane_operation", route)
    runner, event = object(), object()
    assert await seam.session_opened(runner, event, "key", True) is None
    assert [kind for kind, _ in seen] == ["session_opened"]
    assert {"runner", "event", "session_key", "is_new"} <= set(seen[0][1])


@pytest.mark.asyncio
async def test_a_raising_callback_is_isolated_and_the_turn_goes_on(monkeypatch):
    ctx = _context(monkeypatch)

    async def route(kind, **kwargs):
        raise RuntimeError("plugin bug")
    ctx.register_hook("route_lane_operation", route)
    assert await seam.session_opened(object(), object(), "key", True) is None
