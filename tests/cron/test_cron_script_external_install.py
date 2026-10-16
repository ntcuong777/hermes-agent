"""A Nix (``updateMechanism: external``) install runs cron ``.py`` scripts on its own interpreter.

The Nix stamp names no tool store, so ``store_root`` falls back to ``~/.hermes/tools``; a PM
Python left there by another install made the runner take the managed-store path and ask for a
dependency venv inside the read-only package, which never exists: every script job failed with
"dependency environment interpreter is missing" (C149). No file is written here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cron import scheduler_script


@pytest.fixture
def stray_store(monkeypatch):
    """A PM tool store is found (the stray ``~/.hermes/tools``), its venv is not."""
    monkeypatch.setattr("hermes_cli._launchers.resolve_store_python", lambda repo: Path(sys.executable))
    monkeypatch.setattr("pm.environments.selected_venv", lambda repo: Path("/nonexistent/hermes-venv"))


def _stamp(monkeypatch, mechanism):
    monkeypatch.setattr("hermes_cli.steward.read_install_stamp", lambda root: {"updateMechanism": mechanism})


@pytest.mark.platforms("posix")
def test_external_install_runs_scripts_on_its_own_interpreter(stray_store, monkeypatch):
    _stamp(monkeypatch, "external")
    script = Path("/jobs/scripts/watch.py")
    argv, env = scheduler_script._posix_cron_script_argv(script)
    assert argv == [sys.executable, str(script)]
    assert env == {}


@pytest.mark.platforms("posix")
def test_self_managed_install_still_refuses_a_missing_venv(stray_store, monkeypatch):
    # The managed-store rule stays: never a silent run on the bare store Python (#123044).
    _stamp(monkeypatch, "self")
    with pytest.raises(RuntimeError, match="dependency environment interpreter is missing"):
        scheduler_script._posix_cron_script_argv(Path("/jobs/scripts/watch.py"))
