"""A session a messaging lane took over still receives its durable background notices.

A platform ``/resume`` (the session mirror's bind: ``/resume <sid> --all`` from the bound thread)
moves a local conversation onto the lane: the row's identity is rewritten and its local receipt is
retired (``release_receipts`` — the lane restores it from its route). Two things then broke for
every completion/watch notice already addressed to it:

- a notice frozen on the OLD local route key chased a receipt the move deliberately deleted
  (``storage_unavailable`` at ``local_receipt``, retried forever at zero net attempts);
- a notice keyed on the lane's own route was refused ``not_found`` — permanently and silently —
  because the messaging wake borrows a committed ``native_text_v1`` envelope and a locally driven
  route (every turn on it is a local operator's) has none and never will.

The contract here: a notice follows the conversation onto its current route, and a locally driven
route admits a wake as its own stored origin — never as a borrowed human speaker (shared routes
still refuse), and a route with no committed input at all still refuses.
"""
from types import SimpleNamespace

import pytest

from tests.gateway.test_authority_cold_route_recovery import _Runner

pytestmark = pytest.mark.usefixtures("authority_cwd_in_tmp")  # symbolic profile ids are relative homes


async def _wired_runner(tmp_path, monkeypatch, instance_id):
    """A gateway serving one Discord connector over this test's own store (the
    ``test_automation_notification_category._native_owner`` shape)."""
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.run import GatewayRunner
    from gateway.session_authority import SessionAuthority, initialize_session_authority
    from gateway.session_ingress_context import register_transport_home
    from hermes_state import AsyncSessionDB, SessionDB
    from plugins.platforms.discord.adapter import DiscordAdapter

    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
    runner = GatewayRunner(GatewayConfig(sessions_dir=tmp_path / 'sessions'))
    db = SessionDB(tmp_path / 'state.db')
    runner.session_store._db = db
    runner._session_db = AsyncSessionDB(db)
    authority = await initialize_session_authority(runner, profile_id='default', instance_id=instance_id, db=db)
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False))
    runner.adapters = {Platform.DISCORD: adapter}
    runner._wire_adapter_handlers(adapter)
    register_transport_home(runner, None, tmp_path)
    return runner, adapter, authority


_LANE = '1571457'


async def _mirrored_session(tmp_path, monkeypatch):
    """A local conversation with one local-operator turn, moved onto a Discord thread the way the
    session mirror binds it. Returns (runner, adapter, authority, sid, old_local_key, lane_key)."""
    from gateway.session_contract import SessionActor, SessionRef
    from gateway.session_local import create_local_session

    runner, adapter, authority = await _wired_runner(tmp_path, monkeypatch, 'mirrored')
    owner = SessionActor('owner', 'default', frozenset({'session:create', 'session:read', 'session:submit'}), 'socket')
    ref = create_local_session(authority, owner, {'request_id': 'mirrored-review', 'source': 'gui',
                                                  'cwd': str(tmp_path), 'model': 'frozen', 'toolsets': []})
    sid = ref.session_id
    old_local_key = authority.sessions[sid].route
    # A turn on the moved conversation: the operator is not the session's owner stamp, so the
    # admission commits as a locally driven turn (``local_operator_v1``), like the mirror's.
    operator = SessionActor('operator', 'default', frozenset({'session:submit', 'session:operator'}), 'socket')
    from gateway.session_contract import Submission
    from hermes_state_runtime import claim_session_input, settle_session_input
    await authority.submit(operator, Submission('mirror-turn-1', SessionRef('default', sid),
                                                {'text': '[session mirror] it looks like they are done'}, 'queue'))
    # The turn ran and finished before the bind (a handoff waits for a settled conversation).
    claimed = claim_session_input(authority.db, epoch=authority.epoch, session_id=sid)
    assert 'local_operator_v1' in claimed['payload']
    settle_session_input(authority.db, epoch=authority.epoch, admission_id=claimed['admission_id'],
                         generation=claimed['generation'], outcome='completed')
    lane = adapter.build_source(chat_id=_LANE, chat_type='thread', thread_id=_LANE, user_id='42',
                                user_name='session mirror')
    # The mirror's thread is a shared route by default (``thread_sessions_per_user`` is off): its
    # key names no sender, so the shared-route guard below is the one this lane is subject to.
    from gateway.session import is_shared_multi_user_session
    assert is_shared_multi_user_session(lane)
    from gateway.run_startup import GatewayStartupMixin
    lane_key = runner.session_store._generate_session_key(lane)
    await GatewayStartupMixin._handoff_repoint(
        runner, SimpleNamespace(source=lane), lane_key, sid)
    assert authority.db.get_session(sid)['session_key'] == lane_key  # the row follows the lane
    with authority.db._read_ctx() as conn:  # the receipt the move retired stays retired
        from hermes_state_local import POLICY_PREFIX
        assert conn.execute('SELECT 1 FROM state_meta WHERE key=?',
                            (POLICY_PREFIX + sid,)).fetchone() is None
    return runner, adapter, authority, sid, old_local_key, lane_key


def _delegation(key, name, sid):
    """A durable background-subagent completion for *sid*, addressed to route *key*
    (``tests.gateway.test_completion_admission.pending`` with its parent pinned)."""
    import time
    from tools import async_delegation as delegation
    evt = {'type': 'async_delegation', 'session_key': key, 'delegation_id': name, 'parent_session_id': sid,
           'summary': name, 'status': 'completed', 'dispatched_at': time.time()}
    delegation._persist_dispatch(evt)
    delegation._persist_completion(evt, {'status': 'completed', 'summary': name})
    return evt


@pytest.mark.asyncio
async def test_completion_on_the_lane_route_admits_for_a_locally_driven_route(tmp_path, monkeypatch):
    """A reviewer re-dispatched after the bind (its event names the thread route) reaches the
    session: the route has no native envelope to borrow — every turn is a local operator's — and
    the wake runs as the route's stored origin instead of refusing ``not_found``."""
    from hermes_state_runtime import list_session_admissions
    from tools import async_delegation as delegation

    runner, _adapter, authority, sid, _old_key, lane_key = await _mirrored_session(tmp_path, monkeypatch)
    event = _delegation(lane_key, 'lane-keyed-reviewer', sid)
    assert await runner._deliver_async_delegation_group([event]) is True
    rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
    assert 'lane-keyed-reviewer' in rows[-1]['payload']['text']
    assert delegation.get_durable_delegation(event['delegation_id'])['delivery_state'] == 'delivered'


@pytest.mark.asyncio
async def test_completion_frozen_on_the_old_local_route_follows_the_conversation(tmp_path, monkeypatch):
    """A reviewer dispatched BEFORE the bind froze the local route key. The notice follows the
    conversation: the enricher re-keys it to the row's current route and the delivery lands, where
    the old key could only chase the retired receipt forever (attempts pinned at zero)."""
    from hermes_state_runtime import list_session_admissions
    from tools import async_delegation as delegation

    runner, _adapter, authority, sid, old_local_key, lane_key = await _mirrored_session(tmp_path, monkeypatch)
    event = _delegation(old_local_key, 'pre-move-reviewer', sid)
    runner._enrich_async_delegation_routing(event)
    assert event['session_key'] == lane_key
    assert await runner._deliver_async_delegation_group([event]) is True
    rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
    assert 'pre-move-reviewer' in rows[-1]['payload']['text']
    assert delegation.get_durable_delegation(event['delegation_id'])['delivery_state'] == 'delivered'


@pytest.mark.asyncio
async def test_watch_notice_frozen_on_the_old_local_route_follows_the_conversation(tmp_path, monkeypatch):
    """The watch lane rides the same seam: a pattern notice for the session keyed on the old local
    route injects into the lane instead of refusing the retired receipt every tick."""
    runner, _adapter, authority, sid, old_local_key, _lane_key = await _mirrored_session(tmp_path, monkeypatch)
    from hermes_state_runtime import list_session_admissions
    event = {'type': 'watch_match', 'session_key': old_local_key, 'parent_session_id': sid,
             'session_id': 'proc-watch-1', 'started_at': 1.0, 'event_id': 'watch-1',
             'pattern': 'p', 'output': 'o', 'suppressed': False}
    assert await runner._inject_watch_notification('watch fired', event) is True
    rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
    assert 'watch fired' in rows[-1]['payload']['text']


@pytest.mark.asyncio
async def test_a_route_with_no_committed_input_still_refuses(tmp_path, monkeypatch):
    """The origin fallback is not a blanket admit: a lane route nobody ever spoke or operated on
    still refuses ``not_found``, so a producer cannot wake a chat out of nowhere."""
    runner, adapter, _authority = await _wired_runner(tmp_path, monkeypatch, 'empty')
    source = adapter.build_source(chat_id='99', chat_type='thread', thread_id='99', user_id='42')
    entry = runner.session_store.get_or_create_session(source)
    event = _delegation(entry.session_key, 'cold-route-reviewer', entry.session_id)
    from tools import async_delegation as delegation
    assert await runner._deliver_async_delegation_group([event]) is False
    record = delegation.get_durable_delegation(event['delegation_id'])
    assert record['delivery_state'] == 'pending'


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['another requester', 'a participant spoke'])
async def test_a_shared_route_wakes_as_its_origin_only_for_nobody_else(tmp_path, monkeypatch, case):
    """On a shared route the stored origin is the first contact, not an identity a wake may speak
    for on somebody else's behalf: a result another participant requested, or one on a thread a
    participant has spoken on, never borrows it (no sender laundering), and stays pending."""
    from gateway.platforms.event import MessageEvent
    from gateway.session_ingress_context import native_callback
    from hermes_state_runtime import list_session_admissions
    from tools import async_delegation as delegation

    runner, adapter, authority, sid, _old_key, lane_key = await _mirrored_session(tmp_path, monkeypatch)
    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42,7')  # another participant of the thread
    if case == 'a participant spoke':
        said = adapter.build_source(chat_id=_LANE, chat_type='thread', thread_id=_LANE, user_id='7',
                                    message_id='m7')
        with native_callback(runner, spoken := MessageEvent(text='hi', source=said, message_id='m7'), tmp_path):
            await authority.admit_native(spoken)
    event = dict(_delegation(lane_key, 'shared-lane-reviewer', sid),
                 user_id='7' if case == 'another requester' else '42')
    admitted = len(list_session_admissions(authority.db, session_id=sid, pending_only=False))
    assert await runner._deliver_async_delegation_group([event]) is False
    assert len(list_session_admissions(authority.db, session_id=sid, pending_only=False)) == admitted
    assert delegation.get_durable_delegation(event['delegation_id'])['delivery_state'] == 'pending'


@pytest.fixture(autouse=True)
def _runtime_home(tmp_path, monkeypatch):
    from gateway import run
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(run, '_load_gateway_config', lambda *_path: {
        'platform_toolsets': {'cli': []}, 'model': {'default': 'fixture'}})
