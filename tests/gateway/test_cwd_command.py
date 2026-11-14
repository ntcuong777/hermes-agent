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
from gateway.slash_commands_session import GatewaySessionCommandsMixin

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


# ── list / <n> / prefix completion (handler level) ────────────────────────────


class _Runner:
    """Runner stand-in: session store + the mixin methods under test, bound the way the gateway
    binds them (GatewaySessionCommandsMixin methods run against the GatewayRunner instance).
    ``candidate_workspaces`` is stubbed so the tests drive the handler logic, not projects.db."""

    def __init__(self, tmp_path, candidates, monkeypatch=None):
        self.session_store = _Store()
        self.session_store._entries[KEY] = _entry()
        for name in ('_cwd_candidates_with_db', '_cwd_candidates_reply', '_cwd_pick_candidate'):
            setattr(self, name, getattr(GatewaySessionCommandsMixin, name).__get__(self))
        if monkeypatch is not None:
            fixed = list(candidates)
            monkeypatch.setattr('gateway.workspace_candidates.candidate_workspaces',
                                lambda db=None: fixed)


def test_list_numbers_the_candidates(tmp_path, monkeypatch):
    runner = _Runner(tmp_path, ['/a', '/b'], monkeypatch)
    reply = runner._cwd_candidates_reply(KEY)
    assert '1. `/a`' in reply and '2. `/b`' in reply and '`/cwd <number>`' in reply


def test_list_empty_has_no_numbers(tmp_path, monkeypatch):
    runner = _Runner(tmp_path, [], monkeypatch)
    reply = runner._cwd_candidates_reply(KEY)
    assert '/cwd list' not in reply and '1.' not in reply


def test_numeric_pick_sets_the_nth_candidate(tmp_path, monkeypatch):
    work = tmp_path / 'repo-b'
    work.mkdir()
    runner = _Runner(tmp_path, [str(tmp_path / 'nope'), str(work)], monkeypatch)
    reply = runner._cwd_pick_candidate(KEY, 2)
    assert str(work) in reply
    from tools.terminal_tool import get_session_cwd
    assert get_session_cwd(KEY) == str(work)


def test_numeric_pick_out_of_range_lists_again(tmp_path, monkeypatch):
    runner = _Runner(tmp_path, ['/a'], monkeypatch)
    reply = runner._cwd_pick_candidate(KEY, 5)
    assert 'between 1 and 1' in reply


def test_unique_prefix_expands_through_the_handler_path(tmp_path, monkeypatch):
    """The handler's prefix flow: one basename match expands, sets, and confirms — the phone path
    (`/cwd pers` instead of the full directory)."""
    from gateway.workspace_candidates import match_workspace_prefix
    work = tmp_path / 'papers'
    work.mkdir()
    candidates = [str(tmp_path / 'site'), str(work)]
    matches, note = match_workspace_prefix(candidates, 'pers')
    assert (note, matches) == ('unique', [str(work)])
    runner = _Runner(tmp_path, candidates, monkeypatch)
    reply = runner._cwd_pick_candidate(KEY, 2)  # the expansion sets via the same set_chat_workspace call
    assert str(work) in reply


def test_prefix_matches_basenames_too(tmp_path):
    from gateway.workspace_candidates import match_workspace_prefix
    matches, note = match_workspace_prefix(['/home/me/code/website', '/srv/data'], 'site')
    assert note == 'unique' and matches == ['/home/me/code/website']
    matches, note = match_workspace_prefix(['/x/alpha', '/y/alpine'], 'al')
    assert note == 'ambiguous' and len(matches) == 2


def test_unknown_prefix_falls_through_to_literal_path(tmp_path):
    """No prefix match = empty note: the handler treats the argument as a literal path, so a
    nonexistent one still gets the plain invalid-directory refusal (not a list)."""
    from gateway.workspace_candidates import match_workspace_prefix
    matches, note = match_workspace_prefix(['/home/me/code/website'], 'zzz-nowhere')
    assert (matches, note) == ([], '')
