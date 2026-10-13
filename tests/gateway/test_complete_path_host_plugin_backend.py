"""`complete.path` with a plugin terminal backend that runs on this host (nono).

A plugin backend declares ``is_remote``. ``False`` means its commands run on the gateway
host (a sandbox around local bash), so host paths are valid and the popup reads the host
tree directly. Sending the listing through the sandbox returned nothing, so `@name` and
`@file:` showed no picker. The default (``True``) keeps the backend-addressed listing
that #112963 introduced for ssh/docker.
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


def _register_plugin_backend(monkeypatch, name: str, *, is_remote: bool) -> None:
    from agent import terminal_env_registry
    from agent.terminal_env_provider import TerminalEnvironmentProvider

    backend_name, remote = name, is_remote

    class _Provider(TerminalEnvironmentProvider):
        name = backend_name
        display_name = backend_name
        is_remote = remote

        def is_available(self):
            return True

        def create_environment(self, **kwargs):
            raise AssertionError("completion must not start the backend")

    monkeypatch.setitem(terminal_env_registry._providers, name, _Provider())


def _record_backend_listings(monkeypatch) -> list[str]:
    import tools.terminal_tool as terminal_tool_mod

    calls: list[str] = []

    def fake_terminal_tool(command, **kwargs):
        calls.append(command)
        return '{"output": "", "exit_code": 0}'

    monkeypatch.setattr(terminal_tool_mod, "terminal_tool", fake_terminal_tool)
    return calls


def test_host_plugin_backend_completion_lists_the_host_tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "appChrome.tsx").write_text("x")
    monkeypatch.setenv("TERMINAL_ENV", "hostbox")
    _register_plugin_backend(monkeypatch, "hostbox", is_remote=False)
    calls = _record_backend_listings(monkeypatch)

    fuzzy = _items("@appC")
    listed = _items("@file:")

    assert "@file:appChrome.tsx" in fuzzy, fuzzy
    assert "@file:appChrome.tsx" in listed, listed
    assert calls == []


def test_remote_plugin_backend_completion_still_asks_the_backend(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "host-only.txt").write_text("x")
    monkeypatch.setenv("TERMINAL_ENV", "farbox")
    _register_plugin_backend(monkeypatch, "farbox", is_remote=True)
    calls = _record_backend_listings(monkeypatch)

    texts = _items("host")

    assert texts == [], texts
    assert calls, "listing must go through the backend"
