"""``gateway.model_switch_skew_guard``: what a model switch does when the checkout moved since boot.

``refuse`` (default, the original behaviour) fails the switch with a "restart the gateway" message;
``warn`` performs it and adds a one-line "restart when convenient" note; ``off`` performs it silently.
One read (``gateway.code_skew.model_switch_skew``) serves every model-switch caller: the gateway's
``/model`` and picker (``_perform_model_switch``), a viewer's pick on a lane (``session.mutate``), the TUI's
``model.options`` / ``model.save_key`` RPCs and the dashboard's ``/api/model/options``. A refused pick
records nothing. Cron has no model guard: its skew read only yields a stale ticker's tick to a fresh gateway
(``cron.scheduler.CronTickYielded``), so the key does not touch it.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from gateway import code_skew
from tests.gateway.test_lane_model_survives import (
    CFG, PICK, _bound, _effective, _pick, _resume, _stop)

SKEW = ('abc1234567', 'def4567890')


def _config(mode):
    return {} if mode is None else {'gateway': {'model_switch_skew_guard': mode}}


@pytest.fixture
def stale(monkeypatch):
    monkeypatch.setattr(code_skew, 'detect_code_skew', lambda: SKEW)


@pytest.fixture
def configured(monkeypatch):
    """The gateway's effective config, as the guard reads it on every switch."""
    from gateway import run
    box = {}
    monkeypatch.setattr(run, '_load_gateway_config', lambda *_path: box.get('config', {}))
    return lambda mode: box.__setitem__('config', _config(mode))


@pytest.mark.parametrize(('written', 'mode'), [
    (None, 'refuse'), ('refuse', 'refuse'), ('warn', 'warn'), ('off', 'off'), (False, 'off'), (' WARN ', 'warn'),
    ('sometimes', 'refuse'), (True, 'refuse'), (3, 'refuse'), ({'x': 1}, 'refuse')])
def test_the_mode_reads_the_key_and_anything_unknown_refuses(written, mode):
    assert code_skew.model_switch_skew_mode(_config(written)) == mode


def test_a_config_that_is_not_a_mapping_refuses():
    assert code_skew.model_switch_skew_mode({'gateway': 'warn'}) == 'refuse'
    assert code_skew.model_switch_skew_mode(['gateway']) == 'refuse'


def test_the_default_config_names_the_key_and_defaults_to_refuse():
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    assert DEFAULT_CONFIG['gateway']['model_switch_skew_guard'] == 'refuse'
    assert code_skew.DEFAULT_MODEL_SWITCH_SKEW_MODE == 'refuse'


def test_no_skew_is_never_a_verdict_in_any_mode(monkeypatch, configured):
    from gateway.slash_commands_model import _model_switch_skew_guard, _model_switch_skew_note
    monkeypatch.setattr(code_skew, 'detect_code_skew', lambda: None)
    for mode in (None, 'refuse', 'warn', 'off'):
        configured(mode)
        assert _model_switch_skew_guard() is None and _model_switch_skew_note() is None


@pytest.mark.parametrize(('mode', 'refuses', 'warns'), [
    (None, True, False), ('refuse', True, False), ('warn', False, True), ('off', False, False)])
def test_the_gateway_guard_and_note_follow_the_mode(stale, configured, mode, refuses, warns):
    from gateway.slash_commands_model import _model_switch_skew_guard, _model_switch_skew_note
    configured(mode)
    refusal, note = _model_switch_skew_guard(), _model_switch_skew_note()
    assert (refusal is not None, note is not None) == (refuses, warns)
    if refusal:  # plainly: what to run, and both revisions
        assert 'hermes gateway restart' in refusal and 'restart the gateway' in refusal
        assert SKEW[0] in refusal and SKEW[1] in refusal
    if note:  # one line, no refusal wording
        assert '\n' not in note and 'hermes gateway restart' in note and SKEW[1] in note
        assert 'restart it when convenient' in note


def test_the_key_is_read_on_every_switch_so_an_edit_applies_without_a_restart(stale, configured):
    from gateway.slash_commands_model import _model_switch_skew_guard
    configured('refuse')
    assert _model_switch_skew_guard() is not None
    configured('warn')
    assert _model_switch_skew_guard() is None


def _context(tmp_path):
    from gateway.slash_commands_model import _ModelSwitchContext
    return _ModelSwitchContext(session_key='s', source=None, config_path=tmp_path / 'config.yaml',
                               persist_global=False)


def _switch(tmp_path):
    """``/model`` through the gateway's switch step with only the catalog resolution held offline."""
    from gateway.slash_commands_model import GatewayModelCommandsMixin
    from hermes_cli.model_switch import ModelSwitchResult
    found = ModelSwitchResult(success=True, new_model='picked', target_provider='anthropic')
    switcher = Mock(return_value=found)
    with patch('hermes_cli.model_switch.switch_model', switcher), \
            patch('hermes_cli.context_switch_guard.enrich_model_switch_warnings_for_gateway'):
        outcome = asyncio.run(GatewayModelCommandsMixin._perform_model_switch(
            SimpleNamespace(), _context(tmp_path), 'picked', '', None))
    return outcome, switcher


def test_a_refused_slash_switch_never_reaches_the_resolver_and_says_to_restart(stale, configured, tmp_path):
    configured('refuse')
    (result, error), switcher = _switch(tmp_path)
    assert result is None and 'hermes gateway restart' in error
    switcher.assert_not_called()


@pytest.mark.parametrize(('mode', 'noted'), [('warn', True), ('off', False), (None, None)])
def test_a_slash_switch_in_warn_or_off_mode_is_performed(stale, configured, tmp_path, mode, noted):
    configured(mode)
    (result, error), switcher = _switch(tmp_path)
    if noted is None:  # unset is refuse
        assert result is None and error
        return
    assert error is None and result.new_model == 'picked'
    switcher.assert_called_once()
    assert ('restart it when convenient' in result.warning_message) is noted
    assert noted or result.warning_message == ''


def test_the_note_joins_the_warnings_a_switch_already_carries(stale, configured, tmp_path):
    from gateway.slash_commands_model import GatewayModelCommandsMixin
    from hermes_cli.model_switch import ModelSwitchResult
    configured('warn')
    found = ModelSwitchResult(success=True, new_model='picked', target_provider='anthropic',
                              warning_message='a pricing warning')
    with patch('hermes_cli.model_switch.switch_model', Mock(return_value=found)), \
            patch('hermes_cli.context_switch_guard.enrich_model_switch_warnings_for_gateway'):
        result, _ = asyncio.run(GatewayModelCommandsMixin._perform_model_switch(
            SimpleNamespace(), _context(tmp_path), 'picked', '', None))
    assert result.warning_message.startswith('a pricing warning\n')
    assert 'restart it when convenient' in result.warning_message


# -- a viewer's pick on a lane (session.mutate): the path that refused with model_resolution_failed ----------


def _stale_config(mode):
    return {**CFG, 'gateway': {**CFG['gateway'], **({'model_switch_skew_guard': mode} if mode else {})}}


async def _lane_pick(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(code_skew, 'detect_code_skew', lambda: SKEW)
    world = await _bound(tmp_path, monkeypatch, config=_stale_config(mode))
    snap = await _resume(world)
    reply = await _raw_pick(world, snap, request_id='pick-1')
    return world, snap, reply


async def _raw_pick(world, snap, request_id):
    from hermes_cli.model_switch import ModelSwitchResult
    found = ModelSwitchResult(success=True, new_model=PICK[0], target_provider=PICK[1],
                              base_url='https://anthropic.example/v1', api_mode='chat_completions')
    with patch('hermes_cli.model_switch.switch_model', return_value=found):
        return await world.conn.dispatch({'id': 4, 'method': 'session.mutate', 'params': {
            'session_id': world.sid, 'request_id': request_id, 'expected_revision': snap['revision'],
            'expected_generation': snap['execution_generation'] or 0, 'operation': 'model',
            'payload': {'model': PICK[0], 'provider': PICK[1]}}})


def _receipts(world):
    with world.authority.db._read_ctx() as conn:
        return conn.execute("SELECT COUNT(*) FROM state_meta WHERE key LIKE 'gateway.mutation.v1.%'").fetchone()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', [None, 'refuse'])
async def test_a_refused_lane_pick_says_to_restart_and_records_nothing(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(code_skew, 'detect_code_skew', lambda: None)
    fresh = await _bound(tmp_path, monkeypatch, config=_stale_config(mode))
    _effective(fresh)  # the lane's first turn adopts the session's own route as its saved pick
    before = (_receipts(fresh), fresh.authority.db.get_session(fresh.sid)['runtime_revision'],
              fresh.store.get_model_override(fresh.route), _effective(fresh))
    snap = await _resume(fresh)
    monkeypatch.setattr(code_skew, 'detect_code_skew', lambda: SKEW)  # a commit landed after boot
    reply = await _raw_pick(fresh, snap, 'refused-1')
    error = reply['error']
    assert 'model_resolution_failed' in json.dumps(error), error
    assert error['data']['detail'].endswith('hermes gateway restart'), 'the refusal is not cut short'
    assert len(error['data']['detail']) < 160 and 'Restart it' in error['data']['detail']
    after = (_receipts(fresh), fresh.authority.db.get_session(fresh.sid)['runtime_revision'],
             fresh.store.get_model_override(fresh.route), _effective(fresh))
    assert after == before, 'no receipt, no revision bump, no override, no change of route'
    assert fresh.runner._session_model_override(fresh.route)['model'] == 'claude-sonnet-5-5', 'still its own route'
    # After the restart the same pick goes through (no skew), and records.
    monkeypatch.setattr(code_skew, 'detect_code_skew', lambda: None)
    assert 'error' not in await _raw_pick(fresh, await _resume(fresh), 'refused-1')
    assert _effective(fresh)[:2] == PICK[:2]
    await _stop(fresh)


@pytest.mark.asyncio
async def test_a_warn_mode_lane_pick_is_applied_and_carries_the_restart_note(tmp_path, monkeypatch):
    world, _snap, reply = await _lane_pick(tmp_path, monkeypatch, 'warn')
    assert 'error' not in reply, reply
    note = reply['result']['note']
    assert 'restart it when convenient' in note and '\n' not in note.split('\n', 1)[-1]
    assert note.splitlines()[0], 'the first line still says the switch happened'
    assert _effective(world)[:2] == PICK[:2]
    assert world.store.get_model_override(world.route)['model'] == PICK[0]
    await _stop(world)


@pytest.mark.asyncio
async def test_an_off_mode_lane_pick_is_applied_silently(tmp_path, monkeypatch):
    world, _snap, reply = await _lane_pick(tmp_path, monkeypatch, 'off')
    assert 'error' not in reply, reply
    assert 'note' not in reply['result']
    assert _effective(world)[:2] == PICK[:2]
    await _stop(world)


@pytest.mark.asyncio
async def test_a_warn_mode_pick_without_skew_has_no_note(tmp_path, monkeypatch):
    monkeypatch.setattr(code_skew, 'detect_code_skew', lambda: None)
    world = await _bound(tmp_path, monkeypatch, config=_stale_config('warn'))
    reply = await _raw_pick(world, await _resume(world), 'pick-1')
    assert 'error' not in reply and 'note' not in reply['result']
    await _stop(world)


# -- the TUI model RPCs and the dashboard ----------------------------------------------------------------------


@pytest.mark.parametrize(('mode', 'blocked'), [(None, True), ('refuse', True), ('warn', False), ('off', False)])
def test_the_tui_model_rpcs_follow_the_mode(stale, configured, monkeypatch, tmp_path, mode, blocked):
    from tui_gateway import server
    configured(mode)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    built = []
    monkeypatch.setattr('hermes_cli.inventory.build_model_options_payload',
                        lambda *a, **k: built.append(1) or {'providers': []})
    monkeypatch.setattr(server, '_hermes_home', tmp_path)
    monkeypatch.setattr(server, '_cfg_cache', None)
    monkeypatch.setattr(server, '_cfg_sig', None)
    monkeypatch.setattr(server, '_cfg_path', None)
    reply = server._methods['model.options']('rid', {})
    if blocked:
        assert reply['error']['code'] == 5098 and 'hermes gateway restart' in reply['error']['message']
        assert built == []
    else:
        assert 'error' not in reply and built == [1]


@pytest.mark.parametrize(('mode', 'blocked'), [(None, True), ('refuse', True), ('warn', False), ('off', False)])
def test_the_dashboard_guard_follows_the_mode(stale, configured, mode, blocked):
    from hermes_cli.web_server_config import _dashboard_code_skew_guard
    configured(mode)
    message = _dashboard_code_skew_guard()
    assert (message is not None) is blocked
    assert not blocked or 'restart' in message.lower()
