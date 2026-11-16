"""A local conversation a messaging lane takes over keeps the model it ran.

The Discord session mirror binds a TUI session to a thread with ``/resume <sid> --all``; ``/handoff``
and a platform ``/resume`` move one the same way. The move releases the session's frozen launch
policy, and a lane turn resolved the chat's own ``/model`` pick, else the profile default: a session
created on model A / provider P ran the profile's model B from its first thread turn on. The move
now records the policy's route and the lane adopts it as the chat's pick; a row a lane took over
before that resolves from the route it records.
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.gateway.test_authority_cold_route_recovery import _Runner, _source

pytestmark = pytest.mark.usefixtures("authority_cwd_in_tmp")

PROFILE = {'platform_toolsets': {'cli': []}, 'model': {'default': 'model-b', 'provider': 'openai-codex'}}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    from gateway import run
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(run, '_load_gateway_config', lambda *_path: json.loads(json.dumps(PROFILE)))


def _runtime(provider, target_model=None):
    return {'provider': provider, 'api_key': f'key-{provider}', 'base_url': f'https://{provider}.example/v1',
            'api_mode': 'chat_completions'}


def _gateway_runner(store):
    """The real runner mixins over *store* (the turn's model resolution, no adapters)."""
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner._session_model_overrides = {}
    runner.session_store = store
    runner.config = store.config
    return runner


def _resolve(runner, route):
    with patch('gateway.run._resolve_runtime_agent_kwargs_for_provider', side_effect=_runtime), \
            patch('gateway.run._resolve_runtime_agent_kwargs', return_value=_runtime('openai-codex')):
        model, runtime = runner._resolve_session_agent_runtime(session_key=route, user_config=PROFILE)
    return model, runtime['provider']


async def _bind(tmp_path, params):
    """A local session created with *params*, then bound to a lane by the real repoint."""
    from gateway.run_startup import GatewayStartupMixin
    from gateway.session import AsyncSessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_contract import SessionActor
    from gateway.session_local import create_local_session
    from hermes_state import AsyncSessionDB
    runner = _Runner(tmp_path)
    authority = await initialize_session_authority(runner, profile_id='fixture', instance_id='warm')
    owner = SessionActor('owner', 'fixture', frozenset({'session:create', 'session:read', 'session:control'}), 'socket')
    ref = create_local_session(authority, owner, {'request_id': 'tui-chat', 'source': 'tui', 'cwd': str(tmp_path),
                                                  'toolsets': [], **params})
    source = _source('discord', chat_id='thread-1')
    route = runner.session_store._generate_session_key(source)
    gateway = SimpleNamespace(_session_db=AsyncSessionDB(authority.db), session_authority=authority,
                              async_session_store=AsyncSessionStore(runner.session_store))
    await GatewayStartupMixin._handoff_repoint(gateway, SimpleNamespace(source=source), route, ref.session_id)
    return runner.session_store, route, ref.session_id


@pytest.mark.asyncio
async def test_a_bound_local_session_keeps_its_model_provider_and_reasoning(tmp_path):
    store, route, sid = await _bind(tmp_path, {'model': 'model-a', 'provider': 'anthropic', 'reasoning': 'high'})
    from gateway.session_lane_model import CARRY_PREFIX
    with store._db._read_ctx() as conn:
        saved = conn.execute('SELECT value FROM state_meta WHERE key=?', (CARRY_PREFIX + sid,)).fetchone()
    assert json.loads(saved[0])['model'] == 'model-a'

    runner = _gateway_runner(store)
    assert _resolve(runner, route) == ('model-a', 'anthropic')
    assert store.get_model_override(route)['model'] == 'model-a'  # the chat's persisted pick now
    assert runner._resolve_session_reasoning_config(session_key=route)['effort'] == 'high'

    # After a restart the pick rehydrates like any typed /model.
    restarted = _gateway_runner(store)
    assert _resolve(restarted, route) == ('model-a', 'anthropic')
    assert restarted._resolve_session_reasoning_config(session_key=route)['effort'] == 'high'


@pytest.mark.asyncio
async def test_an_explicit_model_after_the_bind_wins_and_persists(tmp_path):
    store, route, _sid = await _bind(tmp_path, {'model': 'model-a', 'provider': 'anthropic'})
    runner = _gateway_runner(store)
    assert _resolve(runner, route) == ('model-a', 'anthropic')
    # The thread's /model C: in-memory override plus its persisted write-through.
    runner._session_state(route).conversation.model_override = {'model': 'model-c', **_runtime('openrouter')}
    store.set_model_override(route, {'model': 'model-c', 'provider': 'openrouter'})
    assert _resolve(runner, route) == ('model-c', 'openrouter')
    assert _resolve(_gateway_runner(store), route) == ('model-c', 'openrouter')


@pytest.mark.asyncio
async def test_a_pick_made_before_the_first_turn_is_never_replaced(tmp_path):
    store, route, _sid = await _bind(tmp_path, {'model': 'model-a', 'provider': 'anthropic', 'reasoning': 'low'})
    store.set_model_override(route, {'model': 'model-c', 'provider': 'openrouter'})
    runner = _gateway_runner(store)
    assert _resolve(runner, route) == ('model-c', 'openrouter')
    store.set_model_override(route, None)  # /model reset: the profile's, not the carried route
    assert _resolve(_gateway_runner(store), route) == ('model-b', 'openai-codex')


@pytest.mark.asyncio
async def test_a_typed_reasoning_replaces_the_carried_level(tmp_path):
    store, route, _sid = await _bind(tmp_path, {'model': 'model-a', 'provider': 'anthropic', 'reasoning': 'high'})
    runner = _gateway_runner(store)
    _resolve(runner, route)
    runner._set_session_reasoning_override(route, None)  # /reasoning reset
    restarted = _gateway_runner(store)
    reasoning = restarted._resolve_session_reasoning_config(session_key=route)
    assert reasoning is None or reasoning.get('effort') != 'high'


def _seed_bound_row(store, sid, *, created_source, model_config, model='model-a'):
    """A row a lane took over under an older build: lane identity, no carried record, no pick."""
    from gateway.session import SessionEntry
    from datetime import datetime
    source = _source('discord', chat_id='thread-' + sid)
    route = store._generate_session_key(source)
    db = store._db
    db.create_session(sid, source='discord', model=model, session_key=route, chat_id=source.chat_id)
    db._execute_write(lambda conn: conn.execute(
        'UPDATE sessions SET created_source=?, model=?, model_config=? WHERE id=?',
        (created_source, model, json.dumps(model_config), sid)))
    now = datetime.now()
    store._entries[route] = SessionEntry(session_key=route, session_id=sid, created_at=now, updated_at=now,
                                         origin=source, platform=source.platform)
    store._save()
    return route


@pytest.mark.parametrize('shape', ['top-level', 'gateway_runtime'])
def test_an_already_bound_local_row_resolves_its_recorded_route(tmp_path, shape):
    runner = _Runner(tmp_path)
    route_cfg = {'provider': 'anthropic', 'base_url': 'https://api.anthropic.com', 'api_mode': 'anthropic_messages'}
    config = ({**route_cfg, 'model': 'model-a', 'reasoning_config': {'enabled': True, 'effort': 'medium'}}
              if shape == 'top-level' else {'gateway_runtime': route_cfg, 'model': 'model-a'})
    route = _seed_bound_row(runner.session_store, 'local-old', created_source='tui', model_config=config)
    gateway = _gateway_runner(runner.session_store)
    assert _resolve(gateway, route) == ('model-a', 'anthropic')
    if shape == 'top-level':
        assert gateway._resolve_session_reasoning_config(session_key=route)['effort'] == 'medium'


def test_an_ordinary_discord_chat_keeps_the_profile_default(tmp_path):
    runner = _Runner(tmp_path)
    route = _seed_bound_row(runner.session_store, 'discord-chat', created_source='discord',
                            model_config={'gateway_runtime': {'provider': 'anthropic'}}, model='model-a')
    gateway = _gateway_runner(runner.session_store)
    assert _resolve(gateway, route) == ('model-b', 'openai-codex')
    assert runner.session_store.get_model_override(route) is None


def test_an_unavailable_recorded_provider_falls_back_with_a_warning(tmp_path, caplog):
    runner = _Runner(tmp_path)
    route = _seed_bound_row(runner.session_store, 'local-gone', created_source='tui',
                            model_config={'gateway_runtime': {'provider': 'anthropic'}})
    gateway = _gateway_runner(runner.session_store)

    def unavailable(provider, target_model=None):
        if provider == 'anthropic':
            raise RuntimeError('no credentials')
        return _runtime(provider)
    with patch('gateway.run._resolve_runtime_agent_kwargs_for_provider', side_effect=unavailable), \
            patch('gateway.run._resolve_runtime_agent_kwargs', return_value=_runtime('openai-codex')), \
            caplog.at_level('WARNING'):
        model, runtime = gateway._resolve_session_agent_runtime(session_key=route, user_config=PROFILE)
    assert (model, runtime['provider']) == ('model-b', 'openai-codex')
    assert any('unavailable' in record.getMessage() for record in caplog.records if record.levelname == 'WARNING')


@pytest.mark.asyncio
async def test_a_concurrent_caller_waits_for_the_adoption_instead_of_running_on_the_default(tmp_path):
    import threading
    import time
    from gateway import session_lane_model as lane
    store, route, _sid = await _bind(tmp_path, {'model': 'model-a', 'provider': 'anthropic', 'reasoning': 'high'})
    runner = _gateway_runner(store)
    real = lane.carried_route
    results = []

    def slow(db, sid):
        time.sleep(0.3)
        return real(db, sid)
    with patch.object(lane, 'carried_route', slow):
        threads = [threading.Thread(target=lambda: results.append(lane.adopt_carried_model(runner, route)))
                   for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
    assert [r and r.get('model') for r in results] == ['model-a', 'model-a']


@pytest.mark.asyncio
async def test_the_hand_off_record_is_consumed_so_a_later_resume_does_not_revive_it(tmp_path):
    from gateway.session_lane_model import CARRY_PREFIX
    store, route, sid = await _bind(tmp_path, {'model': 'model-a', 'provider': 'anthropic'})
    _resolve(_gateway_runner(store), route)

    def record():
        with store._db._read_ctx() as conn:
            return conn.execute('SELECT 1 FROM state_meta WHERE key=?', (CARRY_PREFIX + sid,)).fetchone()
    assert record() is None


@pytest.mark.asyncio
async def test_a_carried_effort_does_not_outlive_a_model_change_in_the_thread(tmp_path):
    store, route, _sid = await _bind(tmp_path, {'model': 'model-a', 'provider': 'anthropic', 'reasoning': 'high'})
    runner = _gateway_runner(store)
    assert runner._resolve_session_reasoning_config(session_key=route, model='model-a')['effort'] == 'high'
    other = runner._resolve_session_reasoning_config(session_key=route, model='model-c')
    assert other is None or other.get('effort') != 'high'
