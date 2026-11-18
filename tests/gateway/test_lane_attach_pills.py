"""A TUI that attaches to a lane-bound session paints its model and effort pills from the resume reply.

``session.resume`` answered ``info = {desktop_protocol}`` for any mount that is not a LOCAL route (a TUI
session the Discord mirror bound to a thread, a history mount). The TUI never asks ``session.info``, and
the settle frames the authority publishes carry no route, so its status bar showed no model and no effort
until something repainted them (a typed ``/model``). The reply now carries the route the lane's next turn
resolves, from ``local_session_info``.
"""
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
    authority = await _authority(tmp_path, monkeypatch, HOST)
    conn = AuthorityConnection(authority, SimpleNamespace(write=lambda frame: None), {'user_id': 'owner'})
    created = await conn.dispatch({'id': 1, 'method': 'session.create', 'params': {
        'request_id': 'tui', 'source': 'tui', 'cwd': str(authority.persistence.scope.profile_dir)}})
    sid = created['result']['stored_session_id']
    runner = authority.runner
    source = _source('discord', chat_id='thread-1')
    route = runner.session_store._generate_session_key(source)
    gateway = SimpleNamespace(_session_db=authority.persistence.aio, session_authority=authority,
                              async_session_store=AsyncSessionStore(runner.session_store))
    await GatewayStartupMixin._handoff_repoint(gateway, SimpleNamespace(source=source), route, sid)
    await conn.dispatch({'id': 2, 'method': 'session.detach', 'params': {
        'session_id': sid, 'subscription_id': created['result']['subscription_id']}})
    return authority, conn, sid


@pytest.mark.asyncio
async def test_without_the_plugin_a_lane_bound_attach_carries_only_the_protocol_field(tmp_path, monkeypatch):
    """The `lane-mirror` plugin owns the lane's model and effort pills (stage B5): core alone answers like upstream."""
    authority, conn, sid = await _lane_bound(tmp_path, monkeypatch)
    resumed = await conn.dispatch({'id': 3, 'method': 'session.resume', 'params': {'session_id': sid}})
    assert resumed['result']['info'] == {'desktop_protocol': 'hermes-gateway-v1'}
    await conn.close()
    authority.persistence.close()
