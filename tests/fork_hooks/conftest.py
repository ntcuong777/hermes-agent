"""A fresh plugin manager per test, wired as the delivery manager (no discovery, no real plugins)."""

from __future__ import annotations

import pytest

from hermes_cli import plugins


@pytest.fixture
def manager(monkeypatch):
    mgr = plugins.PluginManager()
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: mgr)
    return mgr


@pytest.fixture
def register(manager):
    """register(hook, callback): add a callback the way a plugin's ctx.register_hook ends up."""
    def _register(hook: str, callback) -> None:
        manager._hooks.setdefault(hook, []).append(callback)
    return _register
