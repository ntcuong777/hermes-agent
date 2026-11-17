"""``resolve_write_policy``: plugins may lift the system-prefix rule for a path, or deny one (spec 04).

The deny prefix is pointed at a tmp dir so nothing is written under /etc.
"""

from __future__ import annotations

import json

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
    monkeypatch.setattr(guards, "_hermes_config_resolved", str(tmp_path / "elsewhere" / "config.yaml"))
    monkeypatch.setattr(guards, "_hermes_config_resolved_loaded", True)
    return type("W", (), {"system": system, "repo": repo, "other": other, "tmp": tmp_path})


def _allow_under(root):
    root = str(root)

    def callback(path, system_prefix=False, **_):
        return "allow" if system_prefix and (path == root or path.startswith(root + "/")) else None
    return callback


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
    register(wpp.HOOK, lambda **_: "allow")
    monkeypatch.setattr(guards, "_SENSITIVE_EXACT_PATHS", {str(world.repo / "docker.sock")})
    assert "sensitive system path" in guards._check_sensitive_path(str(world.repo / "docker.sock"))
    config = world.repo / "config.yaml"
    monkeypatch.setattr(guards, "_hermes_config_resolved", str(config))
    assert "Hermes config" in guards._check_sensitive_path(str(config))


def test_a_deny_refuses_a_path_upstream_allows(register, world):
    register(wpp.HOOK, lambda path, **_: "deny" if path.endswith("secret.txt") else None)
    assert "sensitive system path" in guards._check_sensitive_path(str(world.tmp / "secret.txt"))
    assert guards._check_sensitive_path(str(world.tmp / "fine.txt")) is None


def test_a_deny_wins_over_an_allow(register, world):
    register(wpp.HOOK, lambda **_: "allow")
    register(wpp.HOOK, lambda **_: "deny")
    assert "sensitive system path" in guards._check_sensitive_path(str(world.repo / "a.sh"))


def test_a_raising_callback_denies_every_write(register, world):
    def boom(**_):
        raise RuntimeError("policy broke")
    register(wpp.HOOK, lambda **_: "allow")
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
