"""How the host finds the Cursor worker: packaged wrapper first, then config, then a source checkout."""

from __future__ import annotations

import pytest

import agent.cursor_runtime as cr


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("HERMES_CURSOR_WORKER", raising=False)
    monkeypatch.delenv("HERMES_CURSOR_RUNTIME_DIR", raising=False)


def _config(monkeypatch, cursor):
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"cursor": cursor} if cursor is not None else {},
    )


def test_the_packaged_wrapper_env_wins(monkeypatch):
    monkeypatch.setenv("HERMES_CURSOR_WORKER", "/nix/store/x/bin/hermes-cursor-worker")
    monkeypatch.setenv("HERMES_CURSOR_RUNTIME_DIR", "/src/runtime")
    _config(monkeypatch, {"worker": "/cfg/worker"})
    assert cr._worker_command() == ["/nix/store/x/bin/hermes-cursor-worker"]


def test_config_worker_is_next(monkeypatch):
    _config(monkeypatch, {"worker": "/cfg/worker", "runtime_dir": "/src/runtime"})
    assert cr._worker_command() == ["/cfg/worker"]


def test_a_source_checkout_runs_the_entry_with_node(monkeypatch):
    monkeypatch.setenv("HERMES_CURSOR_RUNTIME_DIR", "/src/runtime")
    _config(monkeypatch, {"node": "/opt/node24"})
    assert cr._worker_command() == ["/opt/node24", "/src/runtime/worker-main.ts"]


def test_nothing_configured_is_a_clear_error(monkeypatch):
    _config(monkeypatch, None)
    with pytest.raises(cr.CursorRuntimeError) as err:
        cr._worker_command()
    assert "HERMES_CURSOR_WORKER" in str(err.value)
