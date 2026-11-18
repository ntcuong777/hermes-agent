"""Slash commands from a local surface on a session a messaging lane owns (the Discord session
mirror's bound thread). The local-source gate used to refuse every one with a bare
``permission_denied`` — ``/sandbox off`` and the composer ``/stash`` alike. Now a client-local
plugin command runs here, anything else is relayed to the lane, and with no lane the refusal
stands."""
import asyncio
import time

import pytest

from gateway import session_commands, session_controls
from hermes_state_runtime import RuntimeStoreError


def _run(coro):
    return asyncio.run(coro)


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


def test_register_command_records_its_default_keybinds(monkeypatch):
    from hermes_cli import plugins as plugins_mod
    from hermes_cli.plugins import PluginManager, PluginManifest
    from hermes_cli.plugins_context import PluginContext
    manager = PluginManager()
    monkeypatch.setattr(plugins_mod, '_ensure_plugins_discovered', lambda: manager)
    ctx = PluginContext(PluginManifest(name='pi-commands'), manager)
    ctx.register_command('stashx', handler=lambda raw: '', keybinds={' Alt+S ': ' toggle '})
    ctx.register_command('prx', handler=lambda raw: '')
    commands = plugins_mod.get_plugin_commands()
    assert commands['stashx']['keybinds'] == {'alt+s': 'toggle'}
    assert commands['prx']['keybinds'] == {}


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


def test_memory_pending_is_a_read_and_memory_approve_is_not():
    assert session_commands.command_verdict('memory', 'pending') == 'read'
    assert session_commands.command_verdict('memory', 'approve 1') != 'read'


def _registered(monkeypatch, keybinds):
    from hermes_cli import plugins as plugins_mod
    from hermes_cli.plugins import PluginManager, PluginManifest
    from hermes_cli.plugins_context import PluginContext
    manager = PluginManager()
    monkeypatch.setattr(plugins_mod, '_ensure_plugins_discovered', lambda: manager)
    ctx = PluginContext(PluginManifest(name='pi-commands'), manager)
    ctx.register_command('kb', handler=lambda raw: '', keybinds=keybinds)
    return plugins_mod.get_plugin_commands()['kb']


@pytest.mark.parametrize('bad', [['alt+s'], 'alt+s', 5, {'alt+s'}])
def test_register_command_ignores_keybinds_that_are_not_a_dict_without_raising(monkeypatch, caplog, bad):
    assert _registered(monkeypatch, bad)['keybinds'] == {}
    assert 'keybinds must be a' in caplog.text


def test_register_command_keeps_only_valid_chords_and_warns_about_the_rest(monkeypatch, caplog):
    entry = _registered(monkeypatch, {' Alt+S ': ' toggle ', 'alt+': 'x', '': 'x', 'bad chord': 'x', 'alt+k': 3, 7: 'x',
                                      'ctrl+shift+x': ''})
    assert entry['keybinds'] == {'alt+s': 'toggle', 'ctrl+shift+x': ''}
    assert 'ignored' in caplog.text
