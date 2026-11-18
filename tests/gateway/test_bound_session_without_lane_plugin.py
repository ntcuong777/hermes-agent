"""A session a Discord thread owns, with the `lane-mirror` plugin NOT loaded (spec 08 stage B3).

The harness (real authority, real routing store, real handoff repoint, a duck-typed lane adapter) is the
sweep's own, copied verbatim; the tests pin what core does by itself.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.gateway.test_authority_cold_route_recovery import _Runner
from tests.gateway.test_handoff_local_receipt import _handoff

pytestmark = pytest.mark.usefixtures("authority_cwd_in_tmp")  # symbolic profile ids are relative homes

THREAD = '157999'
_INTERACTIVE = ('session:create', 'session:read', 'session:submit', 'session:control', 'session:approve',
                'session:respond')


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    from gateway import run
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    quick = {'hello': {'type': 'exec', 'command': 'echo hi'}, 'st': {'type': 'alias', 'target': '/status'}}
    monkeypatch.setattr(run, '_load_gateway_config', lambda *_path: {
        'platform_toolsets': {'cli': []}, 'model': {'default': 'fixture'}, 'quick_commands': quick})


class _Lane:
    """The Discord adapter as the session mirror presents it: forwards only for the threads it bound."""

    def __init__(self, bound=()):
        self.bound = set(bound)
        self.forwarded = []

    accepts_local_forward = True

    def owns_local_forward(self, session_id):
        return session_id in self.bound

    async def forward_local_submit(self, *, session_id, text, submission_id, command=False):
        if session_id not in self.bound:
            return None
        self.forwarded.append((session_id, text, command))
        return {'queued': True, 'thread_id': THREAD}


class _MirrorRunner(_Runner):
    def __init__(self, tmp_path, lane):
        super().__init__(tmp_path)
        self.lane = lane

    def _adapter_for_source(self, source):
        from gateway.config import Platform
        if source is not None and source.platform == Platform.DISCORD:
            return self.lane
        return self.adapters.get(source.platform) if source is not None else None


def _thread_source(chat_id=THREAD, user_id='admin-1'):
    from gateway.config import Platform
    from gateway.session import SessionSource
    return SessionSource(platform=Platform.DISCORD, chat_id=chat_id, chat_type='thread', thread_id=chat_id,
                         user_id=user_id, user_name='session mirror', chat_name='Bound', guild_id='157guild')


def _tui(authority, instance):
    """The TUI's socket: an interactive bootstrap grant (``gateway/run_api.py``), so an operator."""
    from gateway.session_controls import AuthorityConnection
    identity = {'user_id': 'uid:501', 'provider': 'local', 'profile_id': 'fixture', 'instance_id': instance,
                'capabilities': list(_INTERACTIVE), 'native_bootstrap': True}
    return AuthorityConnection(authority, object(), identity, operator=True)


async def _local_session(tmp_path, lane):
    from gateway.session_authority import initialize_session_authority
    from gateway.session_local import create_local_session
    tmp_path.mkdir(parents=True, exist_ok=True)
    runner = _MirrorRunner(tmp_path, lane)
    warm = await initialize_session_authority(runner, profile_id='fixture', instance_id='warm')
    tui = _tui(warm, 'warm')
    ref = create_local_session(warm, tui.actor, {'request_id': 'tui', 'source': 'tui', 'cwd': str(tmp_path),
                                                  'model': 'frozen', 'toolsets': []})
    return runner, warm, tui, ref.session_id


async def _bound(tmp_path):
    """A TUI session the mirror bound to its thread, opened by the TUI after a gateway restart."""
    from gateway.session_authority import initialize_session_authority
    lane = _Lane()
    runner, warm, _tui_warm, sid = await _local_session(tmp_path, lane)
    lane.bound.add(sid)
    await _handoff(runner, warm, sid, _thread_source())  # the mirror's `/resume <sid> --all`
    cold = await initialize_session_authority(_MirrorRunner(tmp_path, lane), profile_id='fixture',
                                              instance_id='cold')
    tui = _tui(cold, 'cold')
    return SimpleNamespace(authority=cold, tui=tui, sid=sid, lane=lane)


async def _local(tmp_path):
    lane = _Lane()
    _runner, warm, tui, sid = await _local_session(tmp_path, lane)
    return SimpleNamespace(authority=warm, tui=tui, sid=sid, lane=lane)


async def _foreign(tmp_path, platform='discord'):
    """A messaging chat nobody bound: a Discord (or Telegram) user's own conversation."""
    from gateway.session_authority import initialize_session_authority
    from tests.gateway.test_authority_cold_route_recovery import _seed, _source
    source = _thread_source(chat_id='4242', user_id='someone-else') if platform == 'discord' else _source()
    _seed(tmp_path, 'foreign', source)
    lane = _Lane()  # the same mirror-enabled Discord adapter: it just never bound this chat
    authority = await initialize_session_authority(_MirrorRunner(tmp_path, lane), profile_id='fixture',
                                                   instance_id='cold')
    return SimpleNamespace(authority=authority, tui=_tui(authority, 'cold'), sid='foreign', lane=lane)


async def _call(world, method, **params):
    """One RPC from the TUI's socket on the world's session."""
    reply = await world.tui.dispatch({'id': 1, 'method': method, 'params': {'session_id': world.sid, **params}})
    return reply


def _reason(reply):
    error = (reply or {}).get('error')
    return None if error is None else (error.get('data') or {}).get('reason') or error.get('message')


async def _open(world):
    reply = await _call(world, 'session.resume')
    assert 'error' not in reply, reply
    return reply['result']


# ── the lane-mirror plugin is NOT loaded: core behaves as upstream (spec 08 stage B3) ─────────────────────────
# Stage B3 moved the lane takeover into the `lane-mirror` plugin. This file is the fork's own proof of what core does
# when that plugin is absent, disabled or its patches are skipped: every refusal is upstream's bare
# `permission_denied` (fail-closed). The one exception is a transcript-writing mutation, which upstream never refused:
# it is NOT answered `lane_owned` any more (it runs here), which is why the plugin registers that wrap `critical`.

_REFUSED_WITHOUT_THE_PLUGIN = {
    '!shell': ('shell.exec', lambda s: {'command': 'echo hi'}),
    'file drop': ('file.attach', lambda s: {'path': s['_file']}),
    '/rollback (list)': ('rollback.list', lambda s: {}),
    '/rollback diff': ('rollback.diff', lambda s: {'hash': 'abc123'}),
    '/rollback <n>': ('rollback.restore', lambda s: {'hash': 'abc123'}),
    '/background': ('prompt.background', lambda s: {'text': 'do it later'}),
    '/btw': ('prompt.btw', lambda s: {'text': 'by the way'}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize('label', list(_REFUSED_WITHOUT_THE_PLUGIN))
async def test_without_the_plugin_a_bound_session_refuses_with_upstreams_permission_denied(tmp_path, label):
    method, build = _REFUSED_WITHOUT_THE_PLUGIN[label]
    world = await _bound(tmp_path)
    (tmp_path / 'notes.txt').write_text('dropped')
    snap = {**await _open(world), '_file': str(tmp_path / 'notes.txt')}
    reply = await _call(world, method, **build(snap))
    assert _reason(reply) == 'permission_denied', (label, reply)
    assert world.lane.forwarded == [], (label, world.lane.forwarded)


@pytest.mark.asyncio
async def test_without_the_plugin_a_transcript_write_is_not_turned_into_lane_owned(tmp_path):
    world = await _bound(tmp_path)
    snap = await _open(world)
    reply = await _call(world, 'session.mutate', request_id='req-compress', expected_revision=snap['revision'],
                        operation='compress', payload={}, expected_generation=snap['execution_generation'] or 0)
    assert _reason(reply) != 'lane_owned', reply
    assert world.lane.forwarded == []


@pytest.mark.asyncio
async def test_without_the_plugin_the_seam_hooks_have_no_listener(tmp_path):
    from hermes_cli.plugins import has_hook
    assert not any(has_hook(name) for name in ('observe_session_check', 'wrap_session_call'))


# ── stage B4: the forward is also plugin-owned ───────────────────────────────────────────────────────────────
# Without the plugin an un-attached prompt and a typed command on a lane-bound session are refused, never relayed.


@pytest.mark.asyncio
async def test_without_the_plugin_an_unattached_prompt_is_refused_not_forwarded(tmp_path):
    world = await _bound(tmp_path)
    reply = await _call(world, 'prompt.submit', text='hello there', submission_id='sub-1')
    assert _reason(reply) == 'permission_denied', reply
    assert world.lane.forwarded == []


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['compress', 'status'])
async def test_without_the_plugin_a_typed_command_is_refused_not_forwarded(tmp_path, command):
    world = await _bound(tmp_path)
    await _open(world)
    reply = await _call(world, 'slash.exec', command=command)
    assert _reason(reply) in ('permission_denied', 'unsupported_command'), reply
    assert world.lane.forwarded == []
