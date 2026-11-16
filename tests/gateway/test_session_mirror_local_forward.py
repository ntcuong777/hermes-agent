"""prompt.submit to a mirror-bound messaging session from an un-attached connection: the
canonical store refuses (bare ``permission_denied``), and an adapter offering
``accepts_local_forward`` — the Discord session mirror's bound thread — gets the text relayed
into the lane instead. The answer carries an :class:`AdmissionReceipt`-shaped result so the
submitting client settles it like a queued send (input_id/target_session_id projection). Local
rows keep their own owner rules: no lane, no forward, refusal unchanged."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.session import SessionSource
from gateway.session_authority import LiveSession, SessionAuthority
from gateway.session_controls import AuthorityConnection
from hermes_state import SessionDB
from hermes_state_runtime import begin_runtime_epoch

_ORIGIN = {
    "platform": "discord", "chat_id": "157999", "chat_type": "thread",
    "thread_id": "157999", "user_id": "admin-1", "user_name": "Chris",
    "chat_name": "Bound thread", "scope_id": "157guild", "guild_id": "157guild",
}


def _wired(db, *, origin, adapter):
    """A real authority over *db* holding one session row with *origin*; the runner resolves
    *adapter*; the live session is mounted the way a routed restore would."""
    db.create_session('s', source=str(origin['platform']), chat_id=origin.get('chat_id'),
                      session_key='agent:main:discord:thread:157999:157999',
                      origin_json=json.dumps(origin))
    epoch = begin_runtime_epoch(db, instance_id='current')
    authority = SessionAuthority(SimpleNamespace(_draining=False), profile_id='owned',
                                 instance_id='current', db=db, epoch=epoch)
    authority.sessions['s'] = LiveSession(SessionSource.from_dict(origin),
                                          'agent:main:discord:thread:157999:157999')
    authority.runner = SimpleNamespace(_adapter_for_source=lambda src: adapter)
    identity = {'user_id': 'human', 'profile_id': 'owned', 'instance_id': 'current',
                'capabilities': ['session:read', 'session:submit']}
    return AuthorityConnection(authority, object(), identity)


def _submit(connection):
    return connection.dispatch({'id': 1, 'method': 'prompt.submit', 'params': {
        'session_id': 's', 'text': 'run the red test', 'submission_id': 'sub-1'}})


@pytest.mark.asyncio
async def test_unattached_submit_to_a_bound_session_is_forwarded(tmp_path):
    forward = AsyncMock(return_value={'queued': True, 'thread_id': '157999'})
    adapter = SimpleNamespace(accepts_local_forward=True, forward_local_submit=forward)
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        connection = _wired(db, origin=_ORIGIN, adapter=adapter)
        try:
            submitted = await _submit(connection)
        finally:
            await connection.close()

    assert 'error' not in submitted, submitted
    result = submitted['result']
    assert result['status'] == 'queued'
    assert result['ref'] == {'profile_id': 'owned', 'session_id': 's'}
    assert result['admission_id'].startswith('mirror-forward-')
    forward.assert_awaited_once()
    kwargs = forward.await_args.kwargs
    assert kwargs['session_id'] == 's'
    assert kwargs['text'] == 'run the red test'
    assert kwargs['submission_id'] == 'sub-1'


@pytest.mark.asyncio
async def test_unattached_submit_without_the_flag_stays_refused(tmp_path):
    forward = AsyncMock(return_value={'queued': True, 'thread_id': '157999'})
    adapter = SimpleNamespace(accepts_local_forward=False, forward_local_submit=forward)
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        connection = _wired(db, origin=_ORIGIN, adapter=adapter)
        try:
            submitted = await _submit(connection)
        finally:
            await connection.close()

    assert submitted['error']['code'] == 4001, submitted
    assert submitted['error']['message'] == 'permission_denied'
    assert submitted['error']['data']['reason'] == 'permission_denied'
    forward.assert_not_called()


@pytest.mark.asyncio
async def test_local_session_submit_keeps_the_owner_refusal(tmp_path):
    """A local-origin row has its own owner rules and no lane: the refusal is untouched even
    when the adapter offers a forward."""
    forward = AsyncMock(return_value={'queued': True, 'thread_id': '157999'})
    adapter = SimpleNamespace(accepts_local_forward=True, forward_local_submit=forward)
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        connection = _wired(db, origin=dict(_ORIGIN, platform='local'), adapter=adapter)
        try:
            submitted = await _submit(connection)
        finally:
            await connection.close()

    assert submitted['error']['code'] == 4001, submitted
    assert submitted['error']['message'] == 'permission_denied'
    forward.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('extra', [
    {'model_override': 'gpt-x'},          # would silently run on the lane's model
    {'api_text': 'expanded @file body'},  # model-facing text would be lost
    {'finite': {'max_turns': 1}},         # one-shot semantics would become a chat turn
    {'attachments': [{'path': 'a.png'}]},
    {'bogus': 1},                         # unknown keys: same allowlist as submission()
    {'submission_id': None},              # no request id: a retry could not be deduped
])
async def test_non_plain_submits_keep_the_refusal(tmp_path, extra):
    forward = AsyncMock(return_value={'queued': True, 'thread_id': '157999'})
    adapter = SimpleNamespace(accepts_local_forward=True, forward_local_submit=forward)
    params = {'session_id': 's', 'text': 'run the red test', 'submission_id': 'sub-1', **extra}
    params = {k: v for k, v in params.items() if v is not None}
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        connection = _wired(db, origin=_ORIGIN, adapter=adapter)
        try:
            submitted = await connection.dispatch({'id': 1, 'method': 'prompt.submit', 'params': params})
        finally:
            await connection.close()

    # Refused either by the dispatcher's own param validation (invalid_params) or by
    # the forward guard (the original permission_denied) — never relayed.
    assert submitted['error']['message'] in {'permission_denied', 'invalid_params'}, submitted
    forward.assert_not_called()


@pytest.mark.asyncio
async def test_api_text_equal_to_text_still_forwards(tmp_path):
    forward = AsyncMock(return_value={'queued': True, 'thread_id': '157999'})
    adapter = SimpleNamespace(accepts_local_forward=True, forward_local_submit=forward)
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        connection = _wired(db, origin=_ORIGIN, adapter=adapter)
        try:
            submitted = await connection.dispatch({'id': 1, 'method': 'prompt.submit', 'params': {
                'session_id': 's', 'text': 'go', 'api_text': 'go', 'input_id': 'in-1'}})
        finally:
            await connection.close()

    assert submitted['result']['status'] == 'queued', submitted
    assert forward.await_args.kwargs['submission_id'] == 'in-1'
