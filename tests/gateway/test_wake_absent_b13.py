"""Stage B13 (spec 08b section 9.4): the wake of a moved session is upstream's again when no plugin runs.

The lane-mirror plugin (feature id `wake`) carries the origin wake and the notice retarget. This core no longer has
that code: the automation and notification files are upstream's text, byte for byte, and the plugin's three patches
are the only way a moved session's background result or watch notice is delivered again.

The behavioral wiring below is the plugin's ``test_completion_delivery_moved_session`` (moved out of the fork in B13), copied
here because the fork cannot import the plugin. With the plugin absent, the notice frozen on the old local route stays there
and upstream refuses it.
"""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]

# git blob ids of the four files at upstream main 67e4d22d83b (the sync that moved the upstream base; they were
# at e96221fa73d before it). Equal blobs mean equal bytes.
UPSTREAM_BLOBS = {
    "gateway/session_automation.py": "ae9e68d71f02188609fbba1eccdc5af11827f3ef",
    "gateway/session_ingress_context.py": "6910153a1a2693cdc0cb992e3ac0cdc7abafba7b",
    "gateway/session_local_recovery.py": "ca8a206b10e31fd6422e8ea91110515ceef42c0f",
    "gateway/run_notifications.py": "bd274aea9319ef9c811e8f47963ade2245f6c946",
}


def _git_blob_id(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def test_the_four_wake_files_are_upstream_byte_for_byte():
    """The core files the wake touched are restored to upstream: no origin-wake code is left in them."""
    actual = {name: _git_blob_id(ROOT / name) for name in UPSTREAM_BLOBS}
    assert actual == UPSTREAM_BLOBS


async def _wired_runner(tmp_path, monkeypatch, instance_id):
    """A gateway serving one Discord connector over this test's own store (the
    ``test_automation_notification_category._native_owner`` shape)."""
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.run import GatewayRunner
    from gateway.session_authority import SessionAuthority, initialize_session_authority
    from gateway.session_ingress_context import register_transport_home
    from plugins.platforms.discord.adapter import DiscordAdapter

    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
    runner = GatewayRunner(GatewayConfig(sessions_dir=tmp_path / 'sessions'))
    authority = await initialize_session_authority(runner, profile_id='default', instance_id=instance_id)
    authority.db = _DbView(authority.persistence)
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False))
    runner.adapters = {Platform.DISCORD: adapter}
    runner._wire_adapter_handlers(adapter)
    register_transport_home(runner, None, tmp_path)
    return runner, adapter, authority


class _DbView:
    """``authority.db`` for the assertions: the session row answered by the persistence handle."""

    def __init__(self, persistence):
        self.persistence = persistence

    def get_session(self, session_id):
        entry = self.persistence.sessions.summary(session_id)
        return {} if entry is None else entry.as_list_row()


def claim_session_input(view, *, epoch, session_id):
    """The claim of *session_id*'s next input as a dict (``admission_id``, ``generation``, ``payload``)."""
    claimed = view.persistence.admissions.claim(epoch=epoch, session_id=session_id)
    return {'admission_id': claimed.admission_id, 'generation': claimed.generation, 'payload': dict(claimed.payload)}


def list_session_admissions(view, *, session_id, pending_only=True):
    """The admission rows of *session_id* as dicts (``admission_id``, ``status``, ``payload``)."""
    return [{'admission_id': r.admission_id, 'status': str(r.status), 'payload': dict(r.payload)}
            for r in view.persistence.admissions.list(session_id=session_id, pending_only=pending_only)]


_LANE = '1571457'


async def _mirrored_session(tmp_path, monkeypatch):
    """A local conversation with one local-operator turn, moved onto a Discord thread the way the
    session mirror binds it. Returns (runner, adapter, authority, sid, old_local_key, lane_key)."""
    from gateway.session_contract import SessionActor, SessionRef, Submission
    from gateway.session_local import create_local_session

    runner, adapter, authority = await _wired_runner(tmp_path, monkeypatch, 'mirrored')
    owner = SessionActor('owner', 'default', frozenset({'session:create', 'session:read', 'session:submit'}), 'socket')
    ref = create_local_session(authority, owner, {'request_id': 'mirrored-review', 'source': 'gui',
                                                  'cwd': str(tmp_path), 'model': 'frozen', 'toolsets': []})
    sid = ref.session_id
    old_local_key = authority.sessions[sid].route
    operator = SessionActor('operator', 'default', frozenset({'session:submit', 'session:operator'}), 'socket')
    await authority.submit(operator, Submission('mirror-turn-1', SessionRef('default', sid),
                                                {'text': '[session mirror] it looks like they are done'}, 'queue'))
    claimed = claim_session_input(authority.db, epoch=authority.epoch, session_id=sid)
    assert 'local_operator_v1' in claimed['payload']
    authority.persistence.admissions.settle(epoch=authority.epoch, admission_id=claimed['admission_id'],
                                            generation=claimed['generation'], outcome='completed')
    lane = adapter.build_source(chat_id=_LANE, chat_type='thread', thread_id=_LANE, user_id='42',
                                user_name='session mirror')
    from gateway.run_startup import GatewayStartupMixin
    lane_key = runner.session_store._generate_session_key(lane)
    await GatewayStartupMixin._handoff_repoint(runner, SimpleNamespace(source=lane), lane_key, sid)
    assert authority.db.get_session(sid)['session_key'] == lane_key
    return runner, adapter, authority, sid, old_local_key, lane_key


def _delegation(key, name, sid):
    """A durable background-subagent completion for *sid*, addressed to route *key*."""
    import time
    from tools import async_delegation as delegation
    evt = {'type': 'async_delegation', 'session_key': key, 'delegation_id': name, 'parent_session_id': sid,
           'summary': name, 'status': 'completed', 'dispatched_at': time.time()}
    delegation._persist_dispatch(evt)
    delegation._persist_completion(evt, {'status': 'completed', 'summary': name})
    return evt


@pytest.fixture
def runtime_home(tmp_path, monkeypatch):
    from gateway import run
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(run, '_load_gateway_config', lambda *_path: {
        'platform_toolsets': {'cli': []}, 'model': {'default': 'fixture'}})


@pytest.mark.asyncio
@pytest.mark.usefixtures('authority_cwd_in_tmp', 'runtime_home')
async def test_without_the_plugin_a_notice_frozen_on_the_old_local_route_stays_there_and_upstream_refuses_it(tmp_path, monkeypatch):
    """The representative of the moved-session cases: the plugin's re-key is absent, so the completion keeps the
    old local key, nothing is delivered to the moved conversation, and the notice stays pending (upstream's refusal)."""
    from tools import async_delegation as delegation

    runner, _adapter, authority, sid, old_local_key, _lane_key = await _mirrored_session(tmp_path, monkeypatch)
    admitted = len(list_session_admissions(authority.db, session_id=sid, pending_only=False))
    event = _delegation(old_local_key, 'pre-move-reviewer', sid)
    runner._enrich_async_delegation_routing(event)
    assert event['session_key'] == old_local_key
    assert await runner._deliver_async_delegation_group([event]) is False
    assert len(list_session_admissions(authority.db, session_id=sid, pending_only=False)) == admitted
    assert delegation.get_durable_delegation(event['delegation_id'])['delivery_state'] == 'pending'


@pytest.mark.asyncio
@pytest.mark.usefixtures('authority_cwd_in_tmp', 'runtime_home')
async def test_a_route_with_no_committed_input_still_refuses_without_the_plugin(tmp_path, monkeypatch):
    """The behavioral part of the snapshot refusal: a route nobody ever spoke or operated on still refuses (not_found)."""
    from tools import async_delegation as delegation

    runner, adapter, _authority = await _wired_runner(tmp_path, monkeypatch, 'empty')
    source = adapter.build_source(chat_id='99', chat_type='thread', thread_id='99', user_id='42')
    entry = runner.session_store.get_or_create_session(source)
    event = _delegation(entry.session_key, 'cold-route-reviewer', entry.session_id)
    assert await runner._deliver_async_delegation_group([event]) is False
    assert delegation.get_durable_delegation(event['delegation_id'])['delivery_state'] == 'pending'
