"""A model and effort picked on a lane-owned (Discord thread) session stay until the user changes them.

The session: a TUI session the Discord mirror bound to a thread (``sessions.source='discord'``,
``created_source='tui'``) with a TUI attached. The user picks a model and effort through the real
``session.mutate`` path (a viewer's pick on a messaging chat, ``gateway/session_messaging_picks.py``). Each
test then drives one event that could move the session off that pick and reads what the lane's NEXT turn
resolves (``_resolve_session_agent_runtime`` plus ``_resolve_session_reasoning_config``, the two the turn
calls) and what the TUI pill shows (``session.resume``'s ``info``).

Real: authority, session store, routing index, state.db, the mirror's handoff repoint, the pick's
receipt. Held offline: model catalog resolution (``switch_model``) and credential resolution.
The picked effort (``low``) differs from every default in play (phone model ``medium``, CLI ``high``).
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gateway.session_controls import AuthorityConnection
from tests.gateway.test_authority_cold_route_recovery import _source
from tests.gateway.test_local_session_cli_model import CFG

pytestmark = pytest.mark.usefixtures('authority_cwd_in_tmp')

HOST = 'model:\n  provider: anthropic\n  default: claude-sonnet-5-5\nagent:\n  reasoning_effort: high\n'
PICK = ('claude-opus-5-5', 'anthropic', 'low')
PHONE = ('gpt-6-luna', 'openai-codex')


def _runtime(provider, target_model=None):
    return {'provider': provider, 'api_key': f'key-{provider}', 'base_url': f'https://{provider}.example/v1',
            'api_mode': 'chat_completions'}


async def _boot(tmp_path, monkeypatch, config=CFG, host=HOST):
    """A gateway process over the state the earlier ones left on disk: new store, runner, authority."""
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from hermes_state import AsyncSessionDB
    home = tmp_path / 'home'
    home.mkdir(exist_ok=True)
    (home / 'host.yaml').write_text(host, encoding='utf-8')
    monkeypatch.setattr(run, '_load_gateway_config', lambda *_path: config)
    monkeypatch.setattr(run, '_gateway_config_home', lambda: home)
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = run.GatewayRunner.__new__(run.GatewayRunner)
    runner.adapters, runner.session_store, runner._session_db, runner._draining = {}, store, store._db, False
    runner._session_model_overrides, runner.config = {}, store.config
    authority = await initialize_session_authority(
        runner, profile_id=str(Path(store._db.db_path).parent), instance_id=f'boot-{id(runner)}')
    authority._schedule = lambda ref: None
    # What the real runner holds: the async wrappers its /model write-through awaits.
    runner._session_db = AsyncSessionDB(store._db)
    frames = []  # what the attached TUI is sent
    conn = AuthorityConnection(authority, SimpleNamespace(write=lambda frame: frames.append(frame) or True), {'user_id': 'owner'})
    return SimpleNamespace(authority=authority, runner=runner, store=store, conn=conn, tmp=tmp_path, frames=frames)


async def _stop(world):
    await world.conn.close()
    world.authority.db.close()


async def _bound(tmp_path, monkeypatch, **boot):
    """A TUI session created on the CLI model, bound to a Discord thread by the real handoff repoint."""
    world = await _boot(tmp_path, monkeypatch, **boot)
    created = await world.conn.dispatch({'id': 1, 'method': 'session.create', 'params': {
        'request_id': 'tui', 'source': 'tui', 'cwd': str(Path(world.authority.db.db_path).parent)}})
    sid = created['result']['stored_session_id']
    world.sid, world.source = sid, _source('discord', chat_id='thread-1')
    world.route = world.store._generate_session_key(world.source)
    await _rebind(world, world.source)
    await world.conn.dispatch({'id': 2, 'method': 'session.detach', 'params': {
        'session_id': sid, 'subscription_id': created['result']['subscription_id']}})
    return world


async def _rebind(world, source):
    """The Discord session mirror's ``/resume <sid> --all`` for *source*'s thread."""
    from gateway.run_startup import GatewayStartupMixin
    from hermes_state import AsyncSessionDB
    gateway = SimpleNamespace(_session_db=AsyncSessionDB(world.authority.db), session_authority=world.authority,
                              async_session_store=world.runner.async_session_store)
    route = world.store._generate_session_key(source)
    await GatewayStartupMixin._handoff_repoint(gateway, SimpleNamespace(source=source), route, world.sid)
    return route


async def _resume(world):
    reply = await world.conn.dispatch({'id': 9, 'method': 'session.resume', 'params': {'session_id': world.sid}})
    assert 'error' not in reply, reply
    return reply['result']


async def _pick(world, model=PICK[0], provider=PICK[1], effort=PICK[2], request_id='pick-1'):
    """The TUI's ``/model`` with its effort: a ``session.mutate`` model pick on the attached session."""
    from hermes_cli.model_switch import ModelSwitchResult
    snap = await _resume(world)
    found = ModelSwitchResult(success=True, new_model=model, target_provider=provider,
                              base_url=f'https://{provider}.example/v1', api_mode='chat_completions')
    payload = {'model': model, 'provider': provider, **({'reasoning': effort} if effort else {})}
    with patch('hermes_cli.model_switch.switch_model', return_value=found), \
            patch('gateway.run._resolve_runtime_agent_kwargs_for_provider', side_effect=_runtime):
        reply = await world.conn.dispatch({'id': 4, 'method': 'session.mutate', 'params': {
            'session_id': world.sid, 'request_id': request_id, 'expected_revision': snap['revision'],
            'expected_generation': snap['execution_generation'] or 0, 'operation': 'model',
            'payload': payload}})
    assert 'error' not in reply, reply
    return reply['result']


def _effective(world, route=None, config=CFG):
    """``(model, provider, effort)`` the lane's next turn runs."""
    runner, route = world.runner, route or world.route
    with patch('gateway.run._resolve_runtime_agent_kwargs_for_provider', side_effect=_runtime), \
            patch('gateway.run._resolve_runtime_agent_kwargs', return_value=_runtime('openai-codex')):
        model, runtime = runner._resolve_session_agent_runtime(session_key=route, user_config=config)
    effort = runner._resolve_session_reasoning_config(session_key=route, model=model)
    return model, runtime['provider'], (effort or {}).get('effort')


async def _pill(world):
    info = (await _resume(world))['info']
    return info.get('model'), info.get('reasoning_effort')


@pytest.mark.asyncio
async def test_the_pick_takes_effect_and_is_recorded(tmp_path, monkeypatch):
    world = await _bound(tmp_path, monkeypatch)
    assert _effective(world)[:2] == ('claude-sonnet-5-5', 'anthropic'), 'the session starts on the CLI model'
    receipt = await _pick(world)
    assert (receipt['model'], receipt['provider'], receipt['effort']) == PICK
    assert world.store.get_model_override(world.route)['model'] == PICK[0], 'the lane persisted the pick'
    assert _effective(world) == PICK
    assert await _pill(world) == (PICK[0], PICK[2])
    await _stop(world)


async def _restart(world, monkeypatch, **boot):
    """A new gateway process over the same disk: nothing in memory survives."""
    await _stop(world)
    fresh = await _boot(world.tmp, monkeypatch, **boot)
    fresh.sid, fresh.source, fresh.route = world.sid, world.source, world.route
    return fresh


async def _picked(tmp_path, monkeypatch):
    world = await _bound(tmp_path, monkeypatch)
    await _pick(world)
    return world


@pytest.mark.asyncio
async def test_a_gateway_restart_keeps_the_model_and_effort(tmp_path, monkeypatch):
    world = await _restart(await _picked(tmp_path, monkeypatch), monkeypatch)
    assert _effective(world) == PICK
    assert await _pill(world) == (PICK[0], PICK[2])
    await _stop(world)


@pytest.mark.asyncio
async def test_a_tui_detach_and_resume_keeps_the_model_and_effort(tmp_path, monkeypatch):
    world = await _picked(tmp_path, monkeypatch)
    snap = await _resume(world)
    await world.conn.dispatch({'id': 5, 'method': 'session.detach', 'params': {
        'session_id': world.sid, 'subscription_id': snap['subscription_id']}})
    assert await _pill(world) == (PICK[0], PICK[2])
    assert _effective(world) == PICK
    cold = await _restart(world, monkeypatch)  # the TUI reattaches to a restarted gateway
    assert await _pill(cold) == (PICK[0], PICK[2])
    assert _effective(cold) == PICK
    await _stop(cold)


@pytest.mark.asyncio
async def test_a_discord_message_on_the_thread_keeps_the_model_and_effort(tmp_path, monkeypatch):
    world = await _picked(tmp_path, monkeypatch)
    entry = await world.runner.async_session_store.get_or_create_session(world.source)  # the inbound turn
    assert entry.session_id == world.sid
    assert _effective(world) == PICK
    cold = await _restart(world, monkeypatch)
    await cold.runner.async_session_store.get_or_create_session(cold.source)  # first message after a restart
    assert _effective(cold) == PICK
    await _stop(cold)


@pytest.mark.asyncio
async def test_the_mirror_binding_the_same_thread_again_keeps_the_model_and_effort(tmp_path, monkeypatch):
    world = await _picked(tmp_path, monkeypatch)
    await _rebind(world, world.source)
    assert _effective(world) == PICK
    cold = await _restart(world, monkeypatch)
    await _rebind(cold, cold.source)
    assert _effective(cold) == PICK
    await _stop(cold)


@pytest.mark.asyncio
async def test_the_mirror_binding_the_session_to_another_thread_keeps_the_model_and_effort(tmp_path, monkeypatch):
    world = await _picked(tmp_path, monkeypatch)
    other = _source('discord', chat_id='thread-2')
    route = await _rebind(world, other)
    assert _effective(world, route) == PICK
    cold = await _restart(world, monkeypatch)
    assert _effective(cold, route) == PICK
    await _stop(cold)


@pytest.mark.asyncio
async def test_a_compression_continuation_keeps_the_model_and_effort(tmp_path, monkeypatch):
    world = await _picked(tmp_path, monkeypatch)
    world.store._db.create_session('tip-1', source='discord', session_key=world.route, parent_session_id=world.sid)
    world.store.switch_session(world.route, 'tip-1', preserve_prompt_pin=True)  # the compression tip repoint
    assert _effective(world) == PICK
    cold = await _restart(world, monkeypatch)
    assert _effective(cold) == PICK, 'the repoint kept both the pick and the effort across a restart'
    await _stop(cold)


@pytest.mark.asyncio
async def test_a_new_conversation_in_another_session_leaves_this_one_alone(tmp_path, monkeypatch):
    world = await _picked(tmp_path, monkeypatch)
    other = _source('discord', chat_id='thread-9')
    other_route = world.store._generate_session_key(other)
    await world.runner.async_session_store.get_or_create_session(other)
    world.store.reset_session(other_route)  # /new in that thread
    await world.runner.async_session_store.get_or_create_session(other)
    assert _effective(world) == PICK
    assert _effective(world, other_route)[:2] == PHONE, 'a chat nobody picked for runs the phone route, as ever'
    cold = await _restart(world, monkeypatch)
    assert _effective(cold) == PICK
    await _stop(cold)


@pytest.mark.asyncio
async def test_another_tui_session_bound_later_keeps_its_own_model_and_this_one_is_unmoved(tmp_path, monkeypatch):
    world = await _picked(tmp_path, monkeypatch)
    made = await world.conn.dispatch({'id': 11, 'method': 'session.create', 'params': {
        'request_id': 'tui-2', 'source': 'tui', 'cwd': str(Path(world.authority.db.db_path).parent)}})
    second = SimpleNamespace(**{**vars(world), 'sid': made['result']['stored_session_id'],
                                'source': _source('discord', chat_id='thread-2')})
    second.route = await _rebind(second, second.source)
    assert _effective(second, second.route)[:2] == ('claude-sonnet-5-5', 'anthropic')
    assert _effective(world) == PICK
    await _stop(world)


@pytest.mark.asyncio
async def test_a_config_change_between_restarts_does_not_move_the_session(tmp_path, monkeypatch):
    world = await _picked(tmp_path, monkeypatch)
    moved = {**CFG, 'model': {'provider': 'zai', 'default': 'glm-5.3-flash'},
             'agent': {'reasoning_effort': 'xhigh'},
             'gateway': {**CFG['gateway'], 'model': {'provider': 'deepseek', 'default': 'deepseek-v4.1-pro',
                                                      'reasoning_effort': 'high'}}}
    cold = await _restart(world, monkeypatch, config=moved,
                          host='model:\n  provider: zai\n  default: glm-5.3-flash\nagent:\n  reasoning_effort: xhigh\n')
    assert _effective(cold, config=moved) == PICK
    assert await _pill(cold) == (PICK[0], PICK[2])
    await _stop(cold)


@pytest.mark.asyncio
async def test_an_automation_turn_on_the_lane_and_a_cron_brief_in_it_keep_the_pick(tmp_path, monkeypatch):
    world = await _picked(tmp_path, monkeypatch)
    # An internal (synthetic) turn resolves like a typed one, from the thread's own source.
    with patch('gateway.run._resolve_runtime_agent_kwargs_for_provider', side_effect=_runtime), \
            patch('gateway.run._resolve_runtime_agent_kwargs', return_value=_runtime('openai-codex')):
        model, runtime = world.runner._resolve_session_agent_runtime(source=world.source, user_config=CFG)
    assert (model, runtime['provider']) == PICK[:2]
    # A cron job's brief mirrored into the session is a transcript row; it names no model.
    world.store.append_to_transcript(world.sid, {'role': 'user', 'content': 'cron brief', 'source': 'cron'})
    assert _effective(world) == PICK
    cold = await _restart(world, monkeypatch)
    assert _effective(cold) == PICK
    await _stop(cold)


@pytest.mark.asyncio
async def test_a_provider_that_will_not_resolve_runs_that_turn_elsewhere_and_the_pick_stays(tmp_path, monkeypatch):
    world = await _picked(tmp_path, monkeypatch)
    cold = await _restart(world, monkeypatch)  # the override rehydrates without credentials after a restart

    def down(provider, target_model=None):
        if provider == 'anthropic':
            raise RuntimeError('credential refresh failed')
        return _runtime(provider)
    with patch('gateway.run._resolve_runtime_agent_kwargs_for_provider', side_effect=down), \
            patch('gateway.run._resolve_runtime_agent_kwargs', return_value=_runtime('openai-codex')):
        model, runtime = cold.runner._resolve_session_agent_runtime(session_key=cold.route, user_config=CFG)
    assert (model, runtime['provider']) == PHONE, 'this one turn runs the default route, with a notice'
    assert cold.runner._pre_agent_fallback_notice
    assert cold.store.get_model_override(cold.route)['model'] == PICK[0], 'the saved pick is untouched'
    assert _effective(cold) == PICK, 'the next turn is back on it'
    await _stop(cold)


@pytest.mark.asyncio
async def test_a_typed_reasoning_reset_is_remembered_too(tmp_path, monkeypatch):
    world = await _picked(tmp_path, monkeypatch)
    world.runner._apply_reasoning_selection(world.route, 'discord', 'reset')
    assert _effective(world)[:2] == PICK[:2]
    assert _effective(world)[2] != PICK[2]
    cold = await _restart(world, monkeypatch)
    assert _effective(cold)[2] != PICK[2], 'a reset does not come back as the old pick after a restart'
    cold.runner._apply_reasoning_selection(cold.route, 'discord', 'high')
    again = await _restart(cold, monkeypatch)
    assert _effective(again) == (PICK[0], PICK[1], 'high'), 'a typed level is a pick like the viewer\'s'
    await _stop(again)


