"""A model picked on a lane-bound session stays until the user changes it.

Guards the paths a chosen model has to survive (the session history behind this: a TUI session the Discord
mirror bound to a thread ran the picked model for days; no unprompted flip is recorded): the lane's
first-turn adoption of the local route, a compression-tip repoint of the routing entry (which keeps the pick
but not the adoption marker), and a gateway restart (a fresh runner reads the persisted pick).
"""
import pytest

from tests.gateway.test_lane_keeps_local_model import _bind, _gateway_runner, _resolve, _runtime

pytestmark = pytest.mark.usefixtures("authority_cwd_in_tmp")


@pytest.mark.asyncio
async def test_a_pick_made_after_the_bind_survives_a_tip_repoint_and_a_restart(tmp_path):
    store, route, sid = await _bind(tmp_path, {'model': 'model-a', 'provider': 'anthropic', 'reasoning': 'high'})
    runner = _gateway_runner(store)
    assert _resolve(runner, route) == ('model-a', 'anthropic')  # the lane adopted the local route
    # The viewer's /model: the in-memory override and its persisted write-through.
    runner._session_state(route).conversation.model_override = {'model': 'model-c', **_runtime('openrouter')}
    store.set_model_override(route, {'model': 'model-c', 'provider': 'openrouter'})
    # A compression continuation: the entry repoints to the new tip, keeping the pick, dropping the marker.
    store._db.create_session('tip-1', source='discord', session_key=route, parent_session_id=sid)
    store.switch_session(route, 'tip-1', preserve_prompt_pin=True)
    assert store.get_session_metadata(route, 'lane_model_carried') is None
    assert _resolve(runner, route) == ('model-c', 'openrouter')
    assert _resolve(_gateway_runner(store), route) == ('model-c', 'openrouter')


@pytest.mark.asyncio
async def test_a_reset_stays_reset_across_a_tip_repoint(tmp_path):
    store, route, sid = await _bind(tmp_path, {'model': 'model-a', 'provider': 'anthropic'})
    assert _resolve(_gateway_runner(store), route) == ('model-a', 'anthropic')
    store.set_model_override(route, None)  # /model reset: the profile's route, not the carried one
    store._db.create_session('tip-1', source='discord', session_key=route, parent_session_id=sid)
    store.switch_session(route, 'tip-1', preserve_prompt_pin=True)
    assert _resolve(_gateway_runner(store), route) == ('model-b', 'openai-codex')
