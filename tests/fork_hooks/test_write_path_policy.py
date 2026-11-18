"""``resolve_write_policy``: plugins may lift the system-prefix rule for a path, or deny one (spec 04).

The deny prefix is pointed at a tmp dir so nothing is written under /etc.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from agent.file_safety import deny_key
from tools import file_tools_write_guards as guards
from tools import write_path_policy as wpp


@pytest.fixture
def world(tmp_path, monkeypatch):
    system = tmp_path / "system"
    repo, other = system / "repo", system / "other"
    for d in (repo / "scripts", other):
        d.mkdir(parents=True)
    monkeypatch.setattr(guards, "_SENSITIVE_PATH_PREFIXES", (deny_key(str(system)) + "/",))  # casefolded on macOS
    # Upstream resolves the config path per call (_get_hermes_config_resolved); pin that getter.
    monkeypatch.setattr(guards, "_get_hermes_config_resolved", lambda: str(tmp_path / "elsewhere" / "config.yaml"))
    return type("W", (), {"system": system, "repo": repo, "other": other, "tmp": tmp_path})


SEAMS_MODULE = "hermes_plugins.config_seams.cs_write_roots"


def _as_config_seams(callback):
    """Make ``callback`` look as the loader names the config-seams plugin's functions (tools/hook_sources.py)."""
    def seams_callback(**kwargs):
        return callback(**kwargs)
    seams_callback.__module__ = SEAMS_MODULE
    return seams_callback


def _allow(**_):
    return "allow"


def _allow_under(root):
    root = str(root)

    def callback(path, system_prefix=False, **_):
        return "allow" if system_prefix and (path == root or path.startswith(root + "/")) else None
    return _as_config_seams(callback)


def test_without_a_callback_the_prefix_denies_as_upstream(manager, world):
    assert "sensitive system path" in guards._check_sensitive_path(str(world.repo / "a.sh"))
    assert guards._check_sensitive_path(str(world.tmp / "free.txt")) is None


def test_an_allow_lifts_the_prefix_for_that_path_only(register, world):
    register(wpp.HOOK, _allow_under(world.repo.resolve()))
    assert guards._check_sensitive_path(str(world.repo / "scripts" / "a.sh")) is None
    assert "sensitive system path" in guards._check_sensitive_path(str(world.other / "x"))


def test_dotdot_and_symlinks_out_of_an_allowed_root_are_denied(register, world):
    register(wpp.HOOK, _allow_under(world.repo.resolve()))
    assert "sensitive system path" in guards._check_sensitive_path(str(world.repo / ".." / "other" / "x"))
    link = world.repo / "scripts" / "out"
    link.symlink_to(world.other)
    assert "sensitive system path" in guards._check_sensitive_path(str(link / "x"))


def test_exact_paths_and_the_config_guard_are_never_lifted(register, world, monkeypatch):
    register(wpp.HOOK, _as_config_seams(_allow))
    monkeypatch.setattr(guards, "_SENSITIVE_EXACT_PATHS", {deny_key(str(world.repo / "docker.sock"))})  # keys are casefolded on macOS
    assert "sensitive system path" in guards._check_sensitive_path(str(world.repo / "docker.sock"))
    config = world.repo / "config.yaml"
    monkeypatch.setattr(guards, "_get_hermes_config_resolved", lambda: str(config))
    assert "Hermes config" in guards._check_sensitive_path(str(config))


def test_a_deny_refuses_a_path_upstream_allows(register, world):
    register(wpp.HOOK, lambda path, **_: "deny" if path.endswith("secret.txt") else None)
    assert "sensitive system path" in guards._check_sensitive_path(str(world.tmp / "secret.txt"))
    assert guards._check_sensitive_path(str(world.tmp / "fine.txt")) is None


def test_a_deny_wins_over_an_allow(register, world):
    register(wpp.HOOK, _as_config_seams(_allow))
    register(wpp.HOOK, lambda **_: "deny")
    assert "sensitive system path" in guards._check_sensitive_path(str(world.repo / "a.sh"))


def test_a_raising_callback_denies_every_write(register, world):
    def boom(**_):
        raise RuntimeError("policy broke")
    register(wpp.HOOK, _as_config_seams(_allow))
    register(wpp.HOOK, boom)
    assert "sensitive system path" in guards._check_sensitive_path(str(world.repo / "a.sh"))
    assert "sensitive system path" in guards._check_sensitive_path(str(world.tmp / "free.txt"))


@pytest.mark.parametrize("junk", ["yes", True, 1, {"action": "allow"}])
def test_a_malformed_verdict_denies(register, world, junk):
    register(wpp.HOOK, lambda **_: junk)
    assert "sensitive system path" in guards._check_sensitive_path(str(world.tmp / "free.txt"))


def test_the_callback_sees_the_documented_fields(register, world):
    seen = []
    register(wpp.HOOK, lambda **kw: seen.append(kw) and None)
    guards._check_sensitive_path(str(world.repo / "a.sh"), task_id="t1")
    assert seen and {"path", "requested_path", "op", "task_id", "system_prefix"} <= set(seen[0])
    assert seen[0]["op"] == "write" and seen[0]["task_id"] == "t1" and seen[0]["system_prefix"] is True
    assert seen[0]["requested_path"] == str(world.repo / "a.sh")


def test_write_file_reaches_the_other_guards_once_a_path_is_allowed(register, world):
    from tools.file_tools import write_file_tool

    register(wpp.HOOK, _allow_under(world.repo.resolve()))
    result = json.loads(write_file_tool(str(world.repo / "scripts" / "daily.sh"), "#!/bin/sh\necho hi\n"))
    assert "sensitive system path" not in json.dumps(result)


def test_an_allow_from_another_plugin_does_not_lift_the_prefix(register, world):
    register(wpp.HOOK, _allow)  # a plugin that is not config-seams
    assert "sensitive system path" in guards._check_sensitive_path(str(world.repo / "a.sh"))
    assert guards._check_sensitive_path(str(world.tmp / "free.txt")) is None  # no opinion elsewhere


def test_a_deny_from_another_plugin_denies_beside_a_config_seams_allow(register, world):
    register(wpp.HOOK, _allow_under(world.repo.resolve()))
    register(wpp.HOOK, lambda path, **_: "deny" if path.endswith("a.sh") else None)
    assert "sensitive system path" in guards._check_sensitive_path(str(world.repo / "a.sh"))
    assert guards._check_sensitive_path(str(world.repo / "b.sh")) is None


def test_a_config_seams_allow_lifts_the_prefix_even_after_a_foreign_allow(register, world):
    register(wpp.HOOK, _allow)
    register(wpp.HOOK, _allow_under(world.repo.resolve()))
    assert guards._check_sensitive_path(str(world.repo / "a.sh")) is None
    assert "sensitive system path" in guards._check_sensitive_path(str(world.other / "x"))


@pytest.mark.parametrize("module, trusted", [
    ("hermes_plugins.config_seams", True), ("hermes_plugins.config_seams.cs_write_roots", True),
    ("hermes_plugins.config_seams__home_0123456789ab.cs_write_roots", True),
    ("hermes_plugins.config_seams_evil", False), ("hermes_plugins.evil.config_seams", False),
    ("config_seams", False), ("", False)])
def test_only_the_config_seams_module_namespace_is_trusted(module, trusted):
    from tools import hook_sources

    def callback(**_):
        return None
    callback.__module__ = module
    assert hook_sources.is_trusted(callback) is trusted


def _slow_callback(seconds, started=None):
    def callback(**_):
        if started is not None:
            started.set()
        time.sleep(seconds)
        return None
    return callback


def test_concurrent_writes_are_not_denied_by_a_slow_callback(register, world):
    """Reproduces the shared gate key: the second thread used to get the skip, i.e. a deny."""
    register(wpp.HOOK, _slow_callback(0.2))
    results, lock = {}, threading.Lock()
    paths = [str(world.tmp / f"f{i}") for i in range(3)]

    def check(path):
        denied = wpp.system_path_denied(path, [path], [path], (str(world.system) + "/",), "t")
        with lock:
            results[path] = denied

    threads = [threading.Thread(target=check, args=(p,)) for p in paths]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert results == {p: False for p in paths}


def test_every_call_carries_its_own_tool_call_id(register, world):
    seen = []
    register(wpp.HOOK, lambda **kw: seen.append(kw["tool_call_id"]))
    for name in ("a", "b", "c"):
        wpp.system_path_denied(name, [name], [name], ("/nowhere/",), "t")
    assert len(seen) == 3 and len(set(seen)) == 3 and all(isinstance(i, str) and i for i in seen)


def test_a_callback_that_times_out_denies_its_path(register, world, monkeypatch):
    from hermes_cli import plugins

    monkeypatch.setattr(plugins, "_resolve_hook_callback_timeout", lambda: 0.1)
    register(wpp.HOOK, _slow_callback(1.0))
    path = str(world.tmp / "slow.txt")
    assert wpp.system_path_denied(path, [path], [path], (str(world.system) + "/",), "t") is True
