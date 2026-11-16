"""A viewer's model and effort pills never go empty when its session is synced to a thread.

The TUI merges every ``session.info`` over what it shows. A lane-owned or history-mounted session whose
agent carried no model (or that had no agent and no policy) published ``model: None`` and no effort, so the
pills vanished the moment the session was synced to a Discord thread.
"""
from types import SimpleNamespace
from unittest.mock import patch

from gateway.config import Platform
from gateway.session import SessionSource
from gateway.session_local import _complete_route


def _live(platform):
    source = None if platform is None else SessionSource(platform=platform, chat_id='thread-1', chat_type='thread')
    return SimpleNamespace(source=source, route='agent:main:discord:thread:1' if source else 'history:sid-1')


def test_a_lane_session_without_a_model_gets_the_route_its_next_turn_resolves():
    info = {'model': None, 'lazy': False}
    with patch('gateway.session_messaging_picks.messaging_route_info',
               lambda runner, live: {'model': 'claude-sonnet-5-5', 'reasoning_effort': 'medium', 'fast': False}):
        _complete_route(SimpleNamespace(runner=object()), _live(Platform.DISCORD), info, 'sid-1')
    assert info['model'] == 'claude-sonnet-5-5'
    assert info['reasoning_effort'] == 'medium' and info['fast'] is False


def test_what_the_session_already_reports_is_never_overwritten():
    info = {'model': 'gpt-6', 'reasoning_effort': 'high'}
    with patch('gateway.session_messaging_picks.messaging_route_info',
               lambda runner, live: {'model': 'other', 'reasoning_effort': 'low'}):
        _complete_route(SimpleNamespace(runner=object()), _live(Platform.DISCORD), info, 'sid-1')
    assert info == {'model': 'gpt-6', 'reasoning_effort': 'high'}
    # An effort of '' (default) is a value, not a gap; only a missing one is filled.
    info = {'model': 'gpt-6', 'reasoning_effort': ''}
    _complete_route(SimpleNamespace(runner=object()), _live(Platform.DISCORD), info, 'sid-1')
    assert info['reasoning_effort'] == ''


def test_a_history_mount_reports_the_route_its_stored_row_records():
    row = {'model': 'claude-opus-5-5',
           'model_config': '{"gateway_runtime": {"provider": "anthropic", "base_url": "https://api.anthropic.com",'
                           ' "api_mode": "anthropic_messages"}, "reasoning_config": {"enabled": true, "effort": "high"}}'}
    authority = SimpleNamespace(runner=object(), db=SimpleNamespace(get_session=lambda sid: row))
    info = {'model': None}
    _complete_route(authority, _live(None), info, 'sid-1')
    assert info['model'] == 'claude-opus-5-5'
    assert info['reasoning_effort'] == 'high'


def test_a_failing_lookup_leaves_the_info_as_it_was():
    info = {'model': None}
    with patch('gateway.session_messaging_picks.messaging_route_info', side_effect=RuntimeError('config unreadable')):
        _complete_route(SimpleNamespace(runner=object()), _live(Platform.DISCORD), info, 'sid-1')
    assert info == {'model': None}


def test_a_local_session_is_left_to_its_own_policy():
    info = {'model': None}
    _complete_route(SimpleNamespace(runner=object()), _live(Platform.LOCAL), info, 'sid-1')
    assert info == {'model': None}
