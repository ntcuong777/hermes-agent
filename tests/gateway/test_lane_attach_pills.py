"""A TUI that attaches to a lane-bound session paints its model and effort pills from the resume reply.

``session.resume`` answered ``info = {desktop_protocol}`` for any mount that is not a LOCAL route (a TUI
session the Discord mirror bound to a thread, a history mount). The TUI never asks ``session.info``, and
the settle frames the authority publishes carry no route, so its status bar showed no model and no effort
until something repainted them (a typed ``/model``). The reply now carries the route the lane's next turn
resolves, from ``local_session_info``.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gateway.session_controls import AuthorityConnection
from tests.gateway.test_authority_cold_route_recovery import _source
from tests.gateway.test_local_session_cli_model import _authority

pytestmark = pytest.mark.usefixtures("authority_cwd_in_tmp")

HOST = 'model:\n  provider: anthropic\n  default: claude-opus-5-5\nagent:\n  reasoning_effort: high\n'


async def _lane_bound(tmp_path, monkeypatch):
    """A TUI session created on the CLI model, then bound to a Discord thread by the real repoint."""
    from gateway.run_startup import GatewayStartupMixin
    from gateway.session import AsyncSessionStore
    from hermes_state import AsyncSessionDB
    authority = await _authority(tmp_path, monkeypatch, HOST)
    conn = AuthorityConnection(authority, SimpleNamespace(write=lambda frame: None), {'user_id': 'owner'})
    created = await conn.dispatch({'id': 1, 'method': 'session.create', 'params': {
        'request_id': 'tui', 'source': 'tui', 'cwd': str(Path(authority.db.db_path).parent)}})
    sid = created['result']['stored_session_id']
    runner = authority.runner
    source = _source('discord', chat_id='thread-1')
    route = runner.session_store._generate_session_key(source)
    gateway = SimpleNamespace(_session_db=AsyncSessionDB(authority.db), session_authority=authority,
                              async_session_store=AsyncSessionStore(runner.session_store))
    await GatewayStartupMixin._handoff_repoint(gateway, SimpleNamespace(source=source), route, sid)
    await conn.dispatch({'id': 2, 'method': 'session.detach', 'params': {
        'session_id': sid, 'subscription_id': created['result']['subscription_id']}})
    return authority, conn, sid


@pytest.mark.asyncio
async def test_resuming_a_lane_bound_session_carries_its_model_and_effort(tmp_path, monkeypatch):
    authority, conn, sid = await _lane_bound(tmp_path, monkeypatch)
    resumed = await conn.dispatch({'id': 3, 'method': 'session.resume', 'params': {'session_id': sid}})
    info = resumed['result']['info']
    assert info['model'] == 'claude-opus-5-5'
    assert info['reasoning_effort'] == 'high'
    assert info['desktop_protocol'] == 'hermes-gateway-v1'
    await conn.close()
    authority.db.close()


@pytest.mark.asyncio
async def test_an_unreadable_route_leaves_the_attach_working(tmp_path, monkeypatch):
    authority, conn, sid = await _lane_bound(tmp_path, monkeypatch)
    with patch('gateway.session_local.local_session_info', side_effect=RuntimeError('config unreadable')):
        resumed = await conn.dispatch({'id': 3, 'method': 'session.resume', 'params': {'session_id': sid}})
    assert 'error' not in resumed
    assert resumed['result']['info'] == {'desktop_protocol': 'hermes-gateway-v1'}
    await conn.close()
    authority.db.close()


@pytest.mark.asyncio
async def test_the_lane_reply_carries_only_the_pill_fields_and_never_a_none():
    """A viewer merges the resume reply over what it holds: a None (or a stray field) blanks its pills."""
    from gateway.session_local_inventory import lane_route_info
    full = {'source': 'discord', 'lazy': True, 'title': None, 'archived': False, 'yolo': False,
            'api_mode': None, 'tool_count': None, 'credential_hint': None, 'profile_id': 'p',
            'model': 'claude-opus-5-5', 'reasoning_effort': 'high', 'reasoning_effort_wire': None,
            'service_tier': '', 'fast': False}
    with patch('gateway.session_local.local_session_info', return_value=full), \
            patch('gateway.session_authorities.owner_scope'):
        info = await lane_route_info(SimpleNamespace(), SimpleNamespace(session_id='s'))
    assert info == {'model': 'claude-opus-5-5', 'reasoning_effort': 'high', 'service_tier': '', 'fast': False}


@pytest.mark.asyncio
async def test_a_stalled_route_read_does_not_stall_the_resume(monkeypatch):
    import threading
    from gateway import session_local_inventory
    monkeypatch.setattr(session_local_inventory, 'LANE_ROUTE_TIMEOUT_S', 0.1)
    release = threading.Event()
    try:
        with patch('gateway.session_local.local_session_info', side_effect=lambda *a: release.wait(5)), \
                patch('gateway.session_authorities.owner_scope'):
            info = await session_local_inventory.lane_route_info(SimpleNamespace(), SimpleNamespace(session_id='s'))
    finally:
        release.set()
    assert info == {}
