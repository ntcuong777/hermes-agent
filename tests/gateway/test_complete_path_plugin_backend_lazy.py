"""`complete.path` before any plugin has loaded (the TUI gateway right after launch).

A plugin terminal backend (nono) only lands in the registry when plugins are discovered, and the gateway
does that on the first agent build, not at start. Until then the registry has no provider for the backend,
`is_remote` read as its default (remote), and `@name` / `@file:` listed through the sandbox and showed
nothing. Judging a backend means loading the plugins that could define it first.
"""

from __future__ import annotations

import pytest

from tui_gateway import server


def _items(word: str) -> list[str]:
    resp = server.handle_request(
        {"id": "1", "method": "complete.path", "params": {"word": word}}
    )
    return [it["text"] for it in resp["result"]["items"]]


@pytest.fixture(autouse=True)
def _hermetic_completion(monkeypatch):
    server._fuzzy_cache.clear()
    monkeypatch.setattr(server, "_launch_configured_cwd", lambda: None)
    yield
    server._fuzzy_cache.clear()


def _provider(name: str, *, is_remote: bool):
    from agent.terminal_env_provider import TerminalEnvironmentProvider

    backend_name, remote = name, is_remote

    class _Provider(TerminalEnvironmentProvider):
        name = backend_name
        display_name = backend_name

        def is_available(self):
            return True

        def create_environment(self, **kwargs):
            raise AssertionError("completion must not start the backend")

    _Provider.is_remote = remote
    return _Provider()


def _discovery_registers(monkeypatch, name: str, *, is_remote: bool) -> list[int]:
    """Plugins load on `discover_plugins()`: that call registers the backend, as the real nono plugin does."""
    from agent import terminal_env_registry

    calls: list[int] = []

    def fake_discover(force=False):
        calls.append(1)
        monkeypatch.setitem(
            terminal_env_registry._providers, name, _provider(name, is_remote=is_remote)
        )

    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", fake_discover)
    return calls


def _no_backend_listing(monkeypatch) -> list[str]:
    import tools.terminal_tool as terminal_tool_mod

    calls: list[str] = []

    def fake_terminal_tool(command, **kwargs):
        calls.append(command)
        return '{"output": "", "exit_code": 0}'

    monkeypatch.setattr(terminal_tool_mod, "terminal_tool", fake_terminal_tool)
    return calls


def test_a_host_backend_not_yet_loaded_still_lists_the_host_tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "alpha.txt").write_text("x")
    monkeypatch.setenv("TERMINAL_ENV", "lazybox")
    discovered = _discovery_registers(monkeypatch, "lazybox", is_remote=False)
    listings = _no_backend_listing(monkeypatch)

    fuzzy = _items("@alp")
    listed = _items("@file:")

    assert "@file:alpha.txt" in fuzzy, fuzzy
    assert "@file:alpha.txt" in listed, listed
    assert listings == []
    assert discovered, "the plugins must have been loaded to learn what the backend is"


def test_an_already_loaded_backend_does_not_trigger_discovery(tmp_path, monkeypatch):
    from agent import terminal_env_registry

    monkeypatch.chdir(tmp_path)
    (tmp_path / "alpha.txt").write_text("x")
    monkeypatch.setenv("TERMINAL_ENV", "loadedbox")
    monkeypatch.setitem(
        terminal_env_registry._providers,
        "loadedbox",
        _provider("loadedbox", is_remote=False),
    )
    discovered = _discovery_registers(monkeypatch, "other", is_remote=False)

    assert "@file:alpha.txt" in _items("@alp")
    assert discovered == []


def test_a_remote_backend_not_yet_loaded_is_still_asked_not_read_from_the_host(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "host-only.txt").write_text("x")
    monkeypatch.setenv("TERMINAL_ENV", "farlazy")
    _discovery_registers(monkeypatch, "farlazy", is_remote=True)
    listings = _no_backend_listing(monkeypatch)

    assert _items("host") == []
    assert listings, "a remote backend's tree is read through the backend"


def test_a_failing_discovery_degrades_to_the_backend_listing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "host-only.txt").write_text("x")
    monkeypatch.setenv("TERMINAL_ENV", "ghostbox")

    def boom(force=False):
        raise RuntimeError("plugin scan failed")

    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", boom)
    listings = _no_backend_listing(monkeypatch)

    assert _items("host") == []
    assert listings
