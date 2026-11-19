"""The upstream `git show` never prompts for credentials and runs in its own session, so a stalled fetch
cannot hang the caller (hermes_cli/core_patches_ast.py _committed_text)."""

from __future__ import annotations

import subprocess

from hermes_cli import core_patches_ast as cpa


def test_git_show_gets_no_stdin_its_own_session_and_no_credential_prompts(tmp_path, monkeypatch):
    calls = []

    def recorder(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 1, "", "")
    monkeypatch.setattr(cpa.subprocess, "run", recorder)
    assert cpa._committed_text(tmp_path, "a" * 40, "pkgx.py") is None
    ((argv, kwargs),) = calls
    assert argv[:3] == ["git", "-C", str(tmp_path)] and argv[3] == "show"
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["start_new_session"] is True
    env = kwargs["env"]
    assert env["GIT_NO_LAZY_FETCH"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ASKPASS"] == "true"
