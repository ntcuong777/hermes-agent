"""The local-wall overlay (P7): `terminal.backend: local` + `terminal.local_wall` builds
the named plugin backend's environment; unset builds the plain local one; a named but
missing backend fails closed."""

import pytest

from tools import local_wall_overlay as overlay
from tools.terminal_tool_backends import _build_local_env


class _FakeProvider:
    def __init__(self, env="ENV-OBJECT"):
        self.env = env
        self.calls = []

    def create_environment(self, **kwargs):
        self.calls.append(kwargs)
        return self.env


class _FakeConfig:
    def __init__(self, terminal):
        self._terminal = terminal

    def get(self, key):
        return self._terminal if key == "terminal" else None


def _set_config(monkeypatch, terminal):
    import hermes_cli.config as config_mod

    monkeypatch.setattr(config_mod, "load_config_readonly", lambda: _FakeConfig(terminal))


def _set_provider(monkeypatch, provider):
    import agent.terminal_env_registry as registry

    monkeypatch.setattr(registry, "get_provider", lambda name, **kw: provider)


def test_unset_key_builds_plain_local(monkeypatch, tmp_path):
    _set_config(monkeypatch, {})
    (tmp_path / "wd").mkdir()
    env_obj = _build_local_env(cwd=str(tmp_path / "wd"), timeout=30, local_config={})
    assert type(env_obj).__name__ == "LocalEnvironment"


def test_empty_key_builds_plain_local(monkeypatch, tmp_path):
    _set_config(monkeypatch, {"local_wall": "  "})
    (tmp_path / "wd").mkdir()
    env_obj = _build_local_env(cwd=str(tmp_path / "wd"), timeout=30, local_config={})
    assert type(env_obj).__name__ == "LocalEnvironment"


def test_named_backend_receives_the_local_session(monkeypatch, tmp_path):
    (tmp_path / "wd").mkdir()
    provider = _FakeProvider()
    _set_config(monkeypatch, {"local_wall": "nono"})
    _set_provider(monkeypatch, provider)
    env_obj = _build_local_env(image=None, cwd=str(tmp_path / "wd"), timeout=45,
                               cc={"cpu": 2}, task_id="task-7", local_config={})
    assert env_obj == "ENV-OBJECT"
    assert provider.calls == [dict(cwd=str(tmp_path / "wd"), timeout=45, task_id="task-7",
                                   image=None, container_config={"cpu": 2})]


def test_a_named_backend_that_is_not_registered_fails_closed(monkeypatch, tmp_path):
    (tmp_path / "wd").mkdir()
    _set_config(monkeypatch, {"local_wall": "nono"})
    _set_provider(monkeypatch, None)
    with pytest.raises(RuntimeError, match="not a registered plugin backend"):
        _build_local_env(cwd=str(tmp_path / "wd"), timeout=30, local_config={})


def test_a_broken_config_read_builds_plain_local(monkeypatch, tmp_path):
    """The key decides the default backend's shape; a config problem must not take
    every local terminal down — it reads as unset (and logs)."""
    import hermes_cli.config as config_mod

    def boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(config_mod, "load_config_readonly", boom)
    (tmp_path / "wd").mkdir()
    env_obj = _build_local_env(cwd=str(tmp_path / "wd"), timeout=30, local_config={})
    assert type(env_obj).__name__ == "LocalEnvironment"


def test_local_wall_backend_reads_the_key(monkeypatch):
    _set_config(monkeypatch, {"backend": "local"})
    assert overlay.local_wall_backend() is None
    _set_config(monkeypatch, {"backend": "local", "local_wall": " Nono "})
    assert overlay.local_wall_backend() == "nono"
    _set_config(monkeypatch, {"backend": "local", "local_wall": 0})
    assert overlay.local_wall_backend() is None


def test_overlay_helper_reports_none_without_the_key(monkeypatch, tmp_path):
    _set_config(monkeypatch, {})
    (tmp_path / "wd").mkdir()
    assert overlay.walled_local_environment(
        image=None, cwd=str(tmp_path / "wd"), timeout=30, cc=None, task_id="t") is None


def test_a_failing_wall_provider_propagates(monkeypatch, tmp_path):
    """Fail closed: a named backend that cannot build its environment raises out of the
    local builder — the local backend never silently falls back to plain local."""
    (tmp_path / "wd").mkdir()

    class _Broken:
        def create_environment(self, **kwargs):
            raise RuntimeError("nono is not on PATH — the command was not started.")

    _set_config(monkeypatch, {"local_wall": "nono"})
    _set_provider(monkeypatch, _Broken())
    with pytest.raises(RuntimeError, match="not on PATH"):
        _build_local_env(cwd=str(tmp_path / "wd"), timeout=30, local_config={})


def test_non_local_backends_never_consult_the_overlay():
    """The overlay is the local builder's seam only: no other built-in builder calls
    it, whatever the config says. Read from the source: running docker's builder needs
    a docker host, which a test must not require."""
    import inspect

    from tools.terminal_tool_backends import _build_docker_env, _build_local_env

    assert "walled_local_environment" in inspect.getsource(_build_local_env)
    assert "walled_local_environment" not in inspect.getsource(_build_docker_env)
