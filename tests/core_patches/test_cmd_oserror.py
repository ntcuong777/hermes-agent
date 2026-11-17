"""`hermes plugins patches --record` with a lock file that cannot be written: a message and exit 2, no traceback
(hermes_cli/core_patches_cmd.py cmd_patches)."""

from __future__ import annotations

import pytest

from hermes_cli import core_patches_cmd as cmd
from hermes_cli import core_patches_prov as prov
from tests.core_patches.test_cmd import _args, _stage


def test_a_lock_write_that_fails_with_oserror_prints_a_message_and_exits_2_without_a_traceback(
        target, no_discovery, tmp_path, monkeypatch, capsys):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    _stage(target, plugin_dir=str(plugin))

    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(prov, "write_lock_entry", broken)
    with pytest.raises(SystemExit) as exited:
        cmd.cmd_patches(_args("--record", "up", "--reason", "demo: upper-case the greeting"))
    captured = capsys.readouterr()
    assert exited.value.code == 2
    assert "disk full" in captured.err and "Traceback" not in captured.err
