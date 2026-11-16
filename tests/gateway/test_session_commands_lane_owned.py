"""Slash commands from a local surface on a session a messaging lane owns (the Discord session
mirror's bound thread). The local-source gate used to refuse every one with a bare
``permission_denied`` — ``/sandbox off`` and the composer ``/stash`` alike. Now a client-local
plugin command runs here, anything else is relayed to the lane, and with no lane the refusal
stands."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway import session_commands, session_controls
from hermes_state_runtime import RuntimeStoreError


def _run(coro):
    return asyncio.run(coro)


def test_client_local_plugin_command_runs_here(monkeypatch):
    monkeypatch.setattr(session_commands, '_client_local_plugin', lambda name: name == 'stash')
    forward = AsyncMock()
    connection = SimpleNamespace(forward_command_to_lane=forward)
    assert _run(session_commands._nonlocal_command(
        connection, SimpleNamespace(session_id='s'), 'stash', 'pop', ('plugin', 'stash'), 'session:control')) is None
    forward.assert_not_called()


def test_other_commands_are_relayed_to_the_lane(monkeypatch):
    monkeypatch.setattr(session_commands, '_client_local_plugin', lambda name: False)
    forward = AsyncMock(return_value={'type': 'exec', 'output': 'sent'})
    connection = SimpleNamespace(forward_command_to_lane=forward)
    result = _run(session_commands._nonlocal_command(
        connection, SimpleNamespace(session_id='s'), 'sandbox', 'off', None, 'session:control'))
    assert result == {'type': 'exec', 'output': 'sent'}
    forward.assert_awaited_once()
    assert forward.await_args.args[1:] == ('/sandbox off', 'session:control')


@pytest.mark.parametrize('connection', [
    SimpleNamespace(forward_command_to_lane=AsyncMock(return_value=None)),  # no forwarding lane
    SimpleNamespace(),                                                       # not an authority connection
])
def test_without_a_lane_the_refusal_stands(monkeypatch, connection):
    monkeypatch.setattr(session_commands, '_client_local_plugin', lambda name: False)
    with pytest.raises(RuntimeStoreError) as refused:
        _run(session_commands._nonlocal_command(
            connection, SimpleNamespace(session_id='s'), 'stash', 'pop', ('plugin', 'stash'), 'session:control'))
    assert refused.value.reason == 'permission_denied'


def test_register_command_records_client_local(monkeypatch):
    from hermes_cli import plugins as plugins_mod
    from hermes_cli.plugins import PluginManager
    from hermes_cli.plugins_context import PluginContext
    manager = PluginManager()
    monkeypatch.setattr(plugins_mod, '_ensure_plugins_discovered', lambda: manager)
    from hermes_cli.plugins import PluginManifest
    ctx = PluginContext(PluginManifest(name='pi-commands'), manager)
    ctx.register_command('stashx', handler=lambda raw: '', client_local=True)
    ctx.register_command('prx', handler=lambda raw: '')
    assert plugins_mod.plugin_command_is_client_local('stashx') is True
    assert plugins_mod.plugin_command_is_client_local('prx') is False
    assert plugins_mod.plugin_command_is_client_local('missing') is False


def test_permission_denied_is_logged_once_per_method_and_session(monkeypatch, caplog):
    session_controls._DENIED_LOGGED.clear()
    exc = RuntimeStoreError('permission_denied')
    with caplog.at_level('INFO', logger='gateway.session_controls'):
        for _ in range(5):
            session_controls._log_refusal('/home/p', 'slash.exec', exc, session_id='s1')
        session_controls._log_refusal('/home/p', 'process.list', exc, session_id='s1')
    lines = [r.getMessage() for r in caplog.records if 'refused permission_denied' in r.getMessage()]
    assert lines == ['slash.exec refused permission_denied for session s1 at ?',
                     'process.list refused permission_denied for session s1 at ?']


def test_permission_denied_log_names_the_raising_check(caplog):
    session_controls._DENIED_LOGGED.clear()

    def guarded_check():
        raise RuntimeStoreError('permission_denied')
    try:
        guarded_check()
    except RuntimeStoreError as exc:
        with caplog.at_level('INFO', logger='gateway.session_controls'):
            session_controls._log_refusal('/home/p', 'session.resume', exc, session_id='s9')
    line = next(r.getMessage() for r in caplog.records if 'refused permission_denied' in r.getMessage())
    assert line.startswith('session.resume refused permission_denied for session s9 at ')
    assert 'test_session_commands_lane_owned.py:' in line
