"""``/cwd`` workspace binding: the chat's terminal directory is settable, not just persistent.

``set_chat_workspace`` writes the same routing-entry metadata ``restore_turn_cwd`` seeds from, so a
bound chat lands in its workspace on the next turn and after a gateway restart; a ``/new`` (fresh
routing entry) drops it. Refusals: missing directory, non-local backend, no routing entry.
"""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.session import SessionEntry
from gateway.session_terminal_cwd import get_chat_workspace, set_chat_workspace

KEY = 'agent:main:telegram:dm:202'


class _Store:
    """SessionStore's metadata write path: set whichever entry is current."""

    def __init__(self):
        self._entries = {}

    def set_session_metadata(self, key, field, value):
        self._entries[key].metadata[field] = value


def _entry():
    return SessionEntry(session_key=KEY, session_id='s1', created_at=datetime(2026, 11, 1),
                        updated_at=datetime(2026, 11, 1))


@pytest.fixture(autouse=True)
def _local_terminal(monkeypatch):
    from tools import terminal_tool
    monkeypatch.setattr(terminal_tool, '_session_cwd', {})
    monkeypatch.setenv('TERMINAL_ENV', 'local')


def _runner(tmp_path):
    store = _Store()
    store._entries[KEY] = _entry()
    return SimpleNamespace(session_store=store)


def test_set_resolves_relative_and_user_paths_and_binds_the_chat(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    work = tmp_path / 'code' / 'foo'
    work.mkdir(parents=True)
    runner = _runner(tmp_path)

    resolved, reason = set_chat_workspace(runner, KEY, '~/code/foo')

    assert reason is None and resolved == str(work)
    assert get_chat_workspace(runner, KEY) == str(work)
    from tools.terminal_tool import get_session_cwd
    assert get_session_cwd(KEY) == str(work)  # the current turn's commands land there immediately


def test_a_missing_directory_is_refused_without_touching_the_binding(tmp_path):
    runner = _runner(tmp_path)
    resolved, reason = set_chat_workspace(runner, KEY, str(tmp_path / 'nope'))
    assert resolved is None and reason == 'missing'
    assert get_chat_workspace(runner, KEY) is None


def test_a_non_local_backend_is_refused(tmp_path, monkeypatch):
    import gateway.session_terminal_cwd as mod
    monkeypatch.setattr(mod, '_local_backend', lambda policy: False)
    runner = _runner(tmp_path)
    resolved, reason = set_chat_workspace(runner, KEY, str(tmp_path))
    assert resolved is None and reason == 'backend'
    assert get_chat_workspace(runner, KEY) is None


def test_a_chat_without_a_routing_entry_is_refused(tmp_path):
    runner = SimpleNamespace(session_store=_Store())  # no entry for KEY
    resolved, reason = set_chat_workspace(runner, KEY, str(tmp_path))
    assert resolved is None and reason == 'no_entry'


def test_an_empty_argument_is_refused(tmp_path):
    runner = _runner(tmp_path)
    assert set_chat_workspace(runner, KEY, '   ') == (None, 'empty')


def test_the_binding_survives_a_fresh_restore_but_a_new_entry_has_none(tmp_path):
    """The whole point of the metadata write: after a restart (empty record) the next turn seeds
    from the binding; and a `/new`'s fresh routing entry carries no binding (existing machinery)."""
    work = tmp_path / 'repo'
    work.mkdir()
    runner = _runner(tmp_path)
    set_chat_workspace(runner, KEY, str(work))
    from tools import terminal_tool
    terminal_tool._session_cwd.clear()  # the restart

    from gateway.session_terminal_cwd import restore_turn_cwd
    assert restore_turn_cwd(runner, KEY, None) is not None
    from tools.terminal_tool import get_session_cwd
    assert get_session_cwd(KEY) == str(work)

    fresh = _entry()
    fresh.metadata.pop('terminal_cwd', None)
    runner.session_store._entries[KEY] = fresh
    assert get_chat_workspace(runner, KEY) is None


def test_cwd_is_a_registered_gateway_command():
    """The registry entry is what puts /cwd in the Telegram command menu and Discord's auto-sync."""
    from hermes_cli.commands import COMMAND_REGISTRY
    (cmd,) = [c for c in COMMAND_REGISTRY if c.name == 'cwd']
    assert cmd.gateway_only and cmd.category == 'Session'
