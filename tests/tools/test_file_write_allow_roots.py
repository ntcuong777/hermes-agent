"""`security.write_allow_roots` exempts named directories from the system-path deny list (P20).

The deny list refuses write_file/patch under /etc and /private/etc. A repo that lives there (a nix-darwin
checkout at /etc/nix-darwin, realpath /private/etc/nix-darwin) could never be written by the agent, whatever
the sandbox allowed. A root is an explicit, absolute, narrow opt-in: only the prefix rule is relaxed, never the
exact paths, the Hermes config guard, the other write guards or a path that escapes the root.

The deny prefix is pointed at a tmp dir so the test needs no write under /etc.
"""

import os

import pytest

from tools import file_tools_write_guards as guards


@pytest.fixture
def world(tmp_path, monkeypatch):
    system = tmp_path / "system"
    repo = system / "repo"
    other = system / "other"
    for d in (repo / "scripts", other):
        d.mkdir(parents=True)
    monkeypatch.setattr(guards, "_SENSITIVE_PATH_PREFIXES", (str(system) + "/",))
    monkeypatch.setattr(
        guards, "_hermes_config_resolved", str(tmp_path / "elsewhere" / "config.yaml")
    )
    monkeypatch.setattr(guards, "_hermes_config_resolved_loaded", True)
    roots = []
    monkeypatch.setattr(
        guards,
        "_system_path_allow_roots",
        lambda: tuple(os.path.realpath(r) for r in roots),
    )
    return type(
        "W",
        (),
        {
            "system": system,
            "repo": repo,
            "other": other,
            "roots": roots,
            "tmp": tmp_path,
        },
    )


def test_without_a_root_the_system_prefix_still_denies(world):
    assert "sensitive system path" in guards._check_sensitive_path(
        str(world.repo / "scripts" / "a.sh")
    )


def test_a_root_exempts_paths_inside_it(world):
    world.roots.append(str(world.repo))
    assert guards._check_sensitive_path(str(world.repo / "scripts" / "a.sh")) is None
    assert guards._check_sensitive_path(str(world.repo)) is None


def test_a_root_does_not_exempt_the_rest_of_the_system_prefix(world):
    world.roots.append(str(world.repo))
    assert "sensitive system path" in guards._check_sensitive_path(
        str(world.other / "x")
    )
    assert "sensitive system path" in guards._check_sensitive_path(
        str(world.system / "passwd")
    )


def test_a_sibling_sharing_the_name_prefix_is_not_inside(world):
    (world.system / "repo-evil").mkdir()
    world.roots.append(str(world.repo))
    assert "sensitive system path" in guards._check_sensitive_path(
        str(world.system / "repo-evil" / "x")
    )


def test_dotdot_out_of_the_root_is_denied(world):
    world.roots.append(str(world.repo))
    sneaky = str(world.repo / "scripts" / ".." / ".." / "other" / "x")
    assert "sensitive system path" in guards._check_sensitive_path(sneaky)


def test_a_symlink_inside_the_root_to_a_denied_place_is_denied(world):
    world.roots.append(str(world.repo))
    link = world.repo / "scripts" / "out"
    link.symlink_to(world.other)
    assert "sensitive system path" in guards._check_sensitive_path(str(link / "x"))


def test_exact_paths_are_never_exempt(world, monkeypatch):
    monkeypatch.setattr(
        guards, "_SENSITIVE_EXACT_PATHS", {str(world.repo / "docker.sock")}
    )
    world.roots.append(str(world.repo))
    assert "sensitive system path" in guards._check_sensitive_path(
        str(world.repo / "docker.sock")
    )


def test_the_hermes_config_guard_still_applies_inside_a_root(world, monkeypatch):
    config = world.repo / "config.yaml"
    monkeypatch.setattr(guards, "_hermes_config_resolved", str(config))
    world.roots.append(str(world.repo))
    assert "Hermes config" in guards._check_sensitive_path(str(config))


# -- which roots are honoured (the real reader, with the config faked) ---------------------------


@pytest.fixture
def reader(monkeypatch):
    """The real `_system_path_allow_roots`, with `security.write_allow_roots` set from the test."""
    from hermes_cli import config as config_mod

    holder = {"value": []}
    monkeypatch.setattr(
        config_mod,
        "load_config",
        lambda: {"security": {"write_allow_roots": holder["value"]}},
    )
    return holder


def roots_for(reader, value):
    reader["value"] = value
    return guards._system_path_allow_roots()


def test_the_reader_keeps_absolute_narrow_roots(reader, tmp_path):
    real = os.path.realpath(str(tmp_path))
    assert roots_for(reader, [str(tmp_path)]) == (real,)


def test_the_reader_resolves_a_symlinked_root_to_its_real_path(reader, tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    (tmp_path / "link").symlink_to(target)
    assert roots_for(reader, [str(tmp_path / "link")]) == (
        os.path.realpath(str(target)),
    )


@pytest.mark.parametrize(
    "value", [None, "a string", 5, {"a": 1}, [], [""], [None], ["relative/dir"]]
)
def test_the_reader_ignores_junk_and_relative_roots(reader, value):
    assert roots_for(reader, value) == ()


@pytest.mark.parametrize(
    "root", ["/", "/etc", "/etc/", "/private", "/private/etc", "/usr", "/boot"]
)
def test_the_reader_refuses_a_root_that_would_lift_a_whole_deny_prefix(reader, root):
    assert roots_for(reader, [root]) == ()


def test_a_root_below_a_deny_prefix_is_honoured(reader):
    assert roots_for(reader, ["/private/etc/nix-darwin"]) == (
        os.path.realpath("/private/etc/nix-darwin"),
    )
    assert roots_for(reader, ["/etc/nix-darwin"]) == (
        os.path.realpath("/etc/nix-darwin"),
    )


def test_a_config_read_failure_means_no_roots(monkeypatch):
    from hermes_cli import config as config_mod

    def boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(config_mod, "load_config", boom)
    assert guards._system_path_allow_roots() == ()


def test_the_default_config_names_no_roots():
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["security"]["write_allow_roots"] == []


def test_write_file_reaches_the_other_guards_once_the_root_exempts_it(
    world, monkeypatch
):
    # The whole tool, not just the guard: the exempted write is refused by nothing in the path check.
    import json

    from tools.file_tools import write_file_tool

    world.roots.append(str(world.repo))
    target = world.repo / "scripts" / "daily.sh"
    result = json.loads(write_file_tool(str(target), "#!/bin/sh\necho hi\n"))
    assert "sensitive system path" not in json.dumps(result)


def test_with_the_default_config_the_system_paths_are_denied_exactly_as_before(
    monkeypatch,
):
    # The same cases tests/tools/test_file_tools.py pins, through the real reader and the real prefixes.
    from hermes_cli import config as config_mod

    monkeypatch.setattr(
        config_mod, "load_config", lambda: {"security": {"write_allow_roots": []}}
    )
    monkeypatch.setattr(guards, "_hermes_config_resolved", "/some/other/path")
    monkeypatch.setattr(guards, "_hermes_config_resolved_loaded", True)
    assert guards._check_sensitive_path("/etc/passwd") is not None
    assert guards._check_sensitive_path("/private/etc/hosts") is not None
    assert (
        guards._check_sensitive_path("/private/etc/nix-darwin/hermes/scripts/a.sh")
        is not None
    )
    assert guards._check_sensitive_path("/private/var/db/secret") is not None
    assert guards._check_sensitive_path("/private/var/folders/xy/T/tmp.txt") is None


def test_the_tracked_repo_root_lets_the_repo_in_and_nothing_else_under_etc(monkeypatch):
    # The configuration this repo ships: the checkout exempt, the rest of /etc and /private/etc not.
    from hermes_cli import config as config_mod

    monkeypatch.setattr(
        config_mod,
        "load_config",
        lambda: {"security": {"write_allow_roots": ["/etc/nix-darwin", "/etc/nixos"]}},
    )
    monkeypatch.setattr(guards, "_hermes_config_resolved", "/some/other/path")
    monkeypatch.setattr(guards, "_hermes_config_resolved_loaded", True)
    assert (
        guards._check_sensitive_path("/private/etc/nix-darwin/hermes/scripts/a.sh")
        is None
    )
    assert guards._check_sensitive_path("/etc/nix-darwin/hermes/scripts/a.sh") is None
    assert guards._check_sensitive_path("/private/etc/hosts") is not None
    assert guards._check_sensitive_path("/etc/sudoers") is not None
    assert guards._check_sensitive_path("/private/etc/nix-darwin/../passwd") is not None
