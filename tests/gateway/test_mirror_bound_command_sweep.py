"""Every slash command a TUI attached to a Discord-mirror-bound session can send (#mirror sweep).

The session mirror binds a local TUI session to a Discord thread (``/resume <sid> --all``): the row
moves onto the thread's lane (``source='discord'``, the thread's session key, the mirror admin as
``user_id``) and the thread becomes its one writer; the TUI stays attached as a viewer. Every check
that once assumed "a session a local surface opens is a LOCAL route" refused such a session with a
bare ``permission_denied``: ``/stash``, ``/sandbox off``, ``/memory pending``, ``/undo``, ``/rollback``,
a dropped file... This file pins the policy for all of them on the real authority, real routing store
and real handoff repoint, after a gateway restart (the cold mount an old bound session opens on):

* reads answer locally, client-only state stays local;
* session settings/metadata the authority owns (title, archive, pin, model, reasoning) apply through
  their normal path (they never run a turn, so one-writer holds);
* anything that runs the agent or writes the conversation is relayed to the bound thread
  (``forward_command_to_lane``), never run here and never refused;

and the negative side: a messaging chat the mirror did not bind (another user's Telegram/Discord
chat) keeps every refusal it had. The registry part enumerates ``COMMAND_REGISTRY`` itself, so a
command added later is swept without touching this file.
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


# ── session.resume: the old bound session opens ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_old_bound_session_opens_in_the_tui_after_a_restart(tmp_path):
    world = await _bound(tmp_path)
    snapshot = await _open(world)
    assert snapshot['session_id'] == world.sid
    from gateway.config import Platform
    assert world.authority.sessions[world.sid].source.platform == Platform.DISCORD  # the thread's lane
    assert world.sid in world.tui.subscriptions  # attached as a viewer


# ── TUI-side commands that call their own RPC instead of slash.exec ───────────────────────────
# (ui-tui/src/app/slash/**: canonicalSessionControls.ts, canonicalSessionCommands.ts, commands/*.ts,
# useMainApp.ts). Class per call: ``here`` answers on this owner (a read, client-only state, or a
# setting/metadata write the authority owns: it runs no turn); ``relay`` writes the conversation, so the
# owner answers ``lane_owned`` and the TUI re-sends the typed command (``relay``'s value) through
# ``slash.exec``, which relays it to the thread (ui-tui createSlashHandler ``guardedErr``).


def _mutation(operation, payload, generation=True):
    def build(snap):
        params = {'request_id': 'req-' + operation, 'expected_revision': snap['revision'], 'operation': operation,
                  'payload': payload}
        if generation:
            params['expected_generation'] = snap['execution_generation'] or 0
        return params
    return build


_GEN = lambda snap: snap['execution_generation'] or 0
_HERE = None

# label -> (method, params builder, relayed typed command or _HERE).
TUI_RPCS = {
    '/stop, Esc (interrupt)': ('session.interrupt', lambda s: {'execution_generation': _GEN(s)}, _HERE),
    '/steer': ('session.steer', lambda s: {'execution_generation': _GEN(s), 'text': 'go left'}, _HERE),
    'redirect': ('session.redirect', lambda s: {'execution_generation': _GEN(s), 'text': 'go right'}, _HERE),
    '/title': ('session.mutate', _mutation('rename', {'title': 'New title'}, generation=False), _HERE),
    '/archive': ('session.mutate', _mutation('archive', {'archived': True}, generation=False), _HERE),
    'pin': ('session.mutate', _mutation('sidebar', {'pinned': True}, generation=False), _HERE),
    '/model': ('session.mutate', _mutation('model', {'model': 'other-model'}), _HERE),
    '/reasoning (pick)': ('session.mutate', _mutation('reasoning', {'effort': 'high'}), _HERE),
    '/compress': ('session.mutate', _mutation('compress', {}), 'compress'),
    '/undo, /retry, /rewind': ('session.mutate', _mutation('rewind', {'target_message_id': 1}), 'undo'),
    '/branch': ('session.mutate', _mutation('branch', {'title': 'fork'}), 'branch fork'),
    '/yolo': ('config.set', lambda s: {'key': 'yolo', 'value': 'on'}, _HERE),
    '/verbose': ('config.set', lambda s: {'key': 'verbose', 'value': 'all'}, _HERE),
    '/reasoning show': ('config.set', lambda s: {'key': 'show_reasoning', 'value': 'on'}, _HERE),
    '/busy': ('config.set', lambda s: {'key': 'busy', 'value': 'queue'}, _HERE),
    'config.get busy': ('config.get', lambda s: {'key': 'busy'}, _HERE),
    'config.get voice': ('config.get', lambda s: {'key': 'voice'}, _HERE),
    'config.get mtime': ('config.get', lambda s: {'key': 'mtime'}, _HERE),
    'config.get reasoning': ('config.get', lambda s: {'key': 'reasoning'}, _HERE),
    'config.get yolo': ('config.get', lambda s: {'key': 'yolo'}, _HERE),
    '/usage (stats)': ('session.stats', lambda s: {}, _HERE),
    '/usage (account)': ('session.account_usage', lambda s: {}, _HERE),
    '/background': ('prompt.background', lambda s: {'text': 'do it later'}, 'bg do it later'),
    '/btw': ('prompt.btw', lambda s: {'text': 'by the way'}, 'btw by the way'),
    '!shell': ('shell.exec', lambda s: {'command': 'echo hi'}, _HERE),
    '/rollback (list), /undo preview': ('rollback.list', lambda s: {}, _HERE),
    '/rollback diff': ('rollback.diff', lambda s: {'hash': 'abc123'}, _HERE),
    '/rollback <n>': ('rollback.restore', lambda s: {'hash': 'abc123'}, 'rollback 1'),
    '/diff session': ('rollback.session_diff', lambda s: {}, _HERE),
    'file drop': ('file.attach', lambda s: {'path': s['_file']}, _HERE),
    'image paste': ('image.attach_bytes', lambda s: {'content_base64': 'aGk=', 'filename': 'a.png'}, _HERE),
    '/agents (list)': ('subagent.list', lambda s: {}, _HERE),
    'process roster': ('process.list', lambda s: {}, _HERE),
    '/stop processes': ('process.stop', lambda s: {}, _HERE),
    'goal/loop cards': ('session.control.read', lambda s: {}, _HERE),
    '/save': ('session.save', lambda s: {}, _HERE),
    '/export': ('session.export', lambda s: {}, _HERE),
    '/context': ('session.context_breakdown', lambda s: {}, _HERE),
    '/reload-mcp': ('reload.mcp', lambda s: {}, _HERE),
    '/reload': ('reload.env', lambda s: {}, _HERE),
    '/skills reload': ('skills.reload', lambda s: {}, _HERE),
    '/tools': ('tools.show', lambda s: {}, _HERE),
    'command catalog': ('commands.catalog', lambda s: {}, _HERE),
    'slash completion': ('complete.slash', lambda s: {'text': '/st'}, _HERE),
    'transcript paging': ('session.transcript', lambda s: {}, _HERE),
    'history': ('session.history', lambda s: {}, _HERE),
    'session.info': ('session.info', lambda s: {}, _HERE),
    'typed prompt (attached)': ('prompt.submit', lambda s: {'text': 'hello there', 'submission_id': 'sub-1'}, _HERE),
}

# What a call that passed every gate may still answer in this harness: no turn ever runs (so a
# generation-fenced control finds none, a CAS read may race the failed fixture turn), and the fixture
# runner implements none of the agent/model machinery a handler reaches once it is authorized.
_PAST_THE_GATES = frozenset({'stale_generation', 'revision_conflict', 'invalid_checkpoint', 'command_failed',
                             'unsupported_terminal_backend', 'session_busy'})


def _fixture_limit(exc):
    """An exception the fixture runner raised after the authority let the call through."""
    return isinstance(exc, AttributeError) and any(
        name in str(exc) for name in ('_MirrorRunner', 'SimpleNamespace'))


async def _attempt(world, method, params):
    try:
        return _reason(await asyncio.wait_for(_call(world, method, **params), 20)), None
    except Exception as exc:  # health: allow BLE001 -- classified by the caller
        return None, exc


@pytest.mark.asyncio
@pytest.mark.parametrize('label', list(TUI_RPCS))
async def test_every_tui_rpc_works_on_a_bound_session(tmp_path, label):
    method, build, relayed = TUI_RPCS[label]
    world = await _bound(tmp_path)
    (tmp_path / 'notes.txt').write_text('dropped')
    snap = {**await _open(world), '_file': str(tmp_path / 'notes.txt')}
    reason, exc = await _attempt(world, method, build(snap))
    assert reason != 'permission_denied', (label, reason)
    if relayed is _HERE:
        assert exc is None or _fixture_limit(exc), (label, exc)
        assert reason is None or reason in _PAST_THE_GATES, (label, reason)
        assert world.lane.forwarded == [], (label, world.lane.forwarded)  # answered here, nothing relayed
        return
    # A conversation write: never committed here; the TUI relays the command it typed.
    assert (reason, exc) == ('lane_owned', None), (label, reason, exc)
    reply = await _call(world, 'slash.exec', command=relayed)
    assert 'error' not in reply, (label, reply)
    assert 'bound thread' in reply['result']['output']
    assert world.lane.forwarded == [(world.sid, '/' + relayed, True)]


@pytest.mark.asyncio
async def test_a_typed_prompt_from_an_unattached_tui_is_relayed_to_the_thread(tmp_path):
    world = await _bound(tmp_path)
    reply = await _call(world, 'prompt.submit', text='hello there', submission_id='sub-1')
    assert reply['result']['status'] == 'queued', reply
    assert world.lane.forwarded == [(world.sid, 'hello there', False)]


# ── registry commands through slash.exec / command.dispatch ───────────────────────────────────


def _arg_forms(definition):
    """Every argument form that can change a command's verdict (``command_verdict``): bare, each
    declared subcommand, the review-queue write verbs, the refused ``/voice tts`` and a free word."""
    from gateway import session_commands as sc
    forms = {'', 'x', *definition.subcommands}
    if definition.name in sc._READ_ARGUMENTS or definition.name in sc._CONTROL_ARGUMENTS:
        forms |= {'pending', 'show', 'list', 'approve x', 'reject x', 'create x', 'reset', 'catalog'}
    if definition.name in sc._REFUSED_ARGUMENTS:
        forms |= {'tts', 'on'}
    if definition.name in sc._OWNER_COMMANDS:
        forms |= {'list', 'add x'}
    return sorted(forms)


def registry_cases():
    from hermes_cli.commands import COMMAND_REGISTRY
    return [(definition.name, arg) for definition in COMMAND_REGISTRY for arg in _arg_forms(definition)]


@pytest.fixture
def ran_here(monkeypatch):
    """The command handlers themselves are other files' tests: here only WHERE a command runs is
    observed. Running it locally records ``ran``; the lane relay records on the lane."""
    from gateway import session_commands as sc
    ran = []

    async def in_band(connection, ref, live, home, canonical, arg, definition, before):
        ran.append(('/' + canonical, arg))
        return {'type': 'exec', 'output': 'ran here'}

    async def runner_command(runner, live, name, arg, kind, shell):
        ran.append(('/' + name, arg))
        return 'ran here'

    def skill(name, arg, route):
        if name != 'myskill':
            raise sc.RuntimeStoreError('unsupported_command')
        ran.append(('/' + name, arg))
        return {'type': 'skill', 'name': name, 'message': 'use the skill', 'display': '/' + name}

    async def info_read(authority, live, canonical):
        ran.append(('/' + canonical, ''))
        return {'type': 'exec', 'output': 'ran here'}

    async def cron(authority, arg):
        ran.append(('/cron', arg))
        return {'type': 'exec', 'output': 'ran here'}

    async def no_tui_read(*_args):
        return None

    async def no_pre_command(*_args):
        return None
    monkeypatch.setattr(sc, '_run_in_band', in_band)
    monkeypatch.setattr(sc, '_run_runner_command', runner_command)
    monkeypatch.setattr(sc, '_skill_directive', skill)
    monkeypatch.setattr(sc, '_tui_read', no_tui_read)
    monkeypatch.setattr(sc, '_pre_command_before', no_pre_command)
    from gateway import session_info_reads, session_cron_commands
    monkeypatch.setattr(session_info_reads, 'run_info_read', info_read)
    monkeypatch.setattr(session_cron_commands, 'run_cron', cron)
    return ran


async def _outcome(world, ran, typed, *, dispatch=False):
    """``ran`` (answered here), ``relayed`` (sent to the thread) or the refusal reason."""
    ran_before, relayed_before = len(ran), len(world.lane.forwarded)
    if dispatch:
        name, _, arg = typed.partition(' ')
        reply = await _call(world, 'command.dispatch', name=name, arg=arg)
    else:
        reply = await _call(world, 'slash.exec', command=typed)
    reason = _reason(reply)
    if reason is not None:
        return reason
    if len(world.lane.forwarded) > relayed_before:
        assert world.lane.forwarded[-1][1:] == ('/' + typed, True)
        return 'relayed'
    assert len(ran) > ran_before, (typed, reply)
    return 'ran'


def _expected(name, arg):
    """The policy for a registry command on a mirror-bound session: a read answers here; everything
    else (a control, a setting, a command a local session would run over its own RPC, one nobody
    reviewed) is relayed to the thread. Never a refusal."""
    from gateway.session_commands import command_verdict
    return 'ran' if command_verdict(name, arg) == 'read' else 'relayed'


@pytest.mark.asyncio
async def test_every_registry_command_on_a_bound_session(tmp_path, ran_here):
    """COMMAND_REGISTRY, enumerated at run time with every verdict-relevant argument form."""
    world = await _bound(tmp_path)
    await _open(world)
    wrong = {}
    for name, arg in registry_cases():
        typed = name + (' ' + arg if arg else '')
        if name == 'cron' and _expected(name, arg) == 'relayed':
            # A profile cron-store write, refused on ANY attached viewer's connection (local sessions
            # too: ``_OWNER_COMMANDS``); the session's lane plays no part in it.
            assert await _outcome(world, ran_here, typed) == 'unsupported_command'
            continue
        got = await _outcome(world, ran_here, typed)
        if got != _expected(name, arg):
            wrong[typed] = got
    assert not wrong, wrong


@pytest.mark.asyncio
async def test_command_dispatch_takes_the_same_path(tmp_path, ran_here):
    world = await _bound(tmp_path)
    await _open(world)
    assert await _outcome(world, ran_here, 'memory pending', dispatch=True) == 'ran'
    assert await _outcome(world, ran_here, 'compress', dispatch=True) == 'relayed'


def _plugins(monkeypatch):
    """A plugin manager holding a client-local composer command and one that acts on the agent."""
    from hermes_cli import plugins as plugins_mod
    from hermes_cli.plugins import PluginManager, PluginManifest
    from hermes_cli.plugins_context import PluginContext
    manager = PluginManager()
    monkeypatch.setattr(plugins_mod, '_ensure_plugins_discovered', lambda: manager)
    ctx = PluginContext(PluginManifest(name='sweep-plugin'), manager)
    ctx.register_command('stashx', handler=lambda raw: 'stashed', client_local=True)  # like pi-commands /stash
    ctx.register_command('sandboxx', handler=lambda raw: 'sandbox off')  # like nono-sandbox /sandbox
    ctx.register_command('recall-logx', handler=lambda raw: 'log')  # like memory-recall /recall-log
    return ctx


@pytest.mark.asyncio
@pytest.mark.parametrize('typed, expected', [
    ('stashx pop', 'ran'),          # client-local plugin command: the calling client's own state
    ('sandboxx off', 'ran'),        # a plugin command: same live session either way, answer here
    ('recall-logx', 'ran'),         # a plugin read like memory-recall /recall-log: answer here
    ('hello', 'relayed'),           # a `type: exec` quick command: a host shell for the session
    ('st', 'ran'),                  # an alias quick command onto /status (a read)
    ('myskill do it', 'relayed'),   # a skill runs the agent
    ('nosuchcommand x', 'relayed'),  # unknown here: the thread's own ingress answers it
])
async def test_plugin_quick_and_skill_commands_on_a_bound_session(tmp_path, ran_here, monkeypatch, typed, expected):
    _plugins(monkeypatch)
    world = await _bound(tmp_path)
    await _open(world)
    assert await _outcome(world, ran_here, typed) == expected


# ── future-proof by construction: what no list here names ──────────────────────────────────────


@pytest.mark.asyncio
async def test_commands_and_methods_added_later_never_answer_permission_denied(tmp_path, ran_here, monkeypatch):
    """A registry command, a plugin command and a canonical RPC method that exist only in this test
    (in no allowlist anywhere): on a mirror-bound session none surfaces permission_denied."""
    from hermes_cli import commands
    from hermes_cli.commands import CommandDef
    future = CommandDef('futurecmd', 'added upstream later', 'Session', args_hint='[x]')
    monkeypatch.setattr(commands, 'COMMAND_REGISTRY', [*commands.COMMAND_REGISTRY, future])
    monkeypatch.setitem(commands._COMMAND_LOOKUP, 'futurecmd', future)
    _plugins(monkeypatch).register_command('futureplugin', handler=lambda raw: 'did it')

    from tui_gateway.contracts import registry
    from tui_gateway.contracts.base import Params, Result

    class FutureParams(Params):
        session_id: str

    class FutureResult(Result):
        value: str

    for name in ('future.read', 'future.write'):
        monkeypatch.setitem(registry.CANONICAL_METHODS, name, registry.MethodContract(name, FutureParams, FutureResult))

    async def future_handler(capability, connection, ref, params):
        # The shape of every handler written for local sessions: authorize, then the frozen policy.
        connection.authority.authorize(connection.actor, ref, capability)
        from gateway.session_policy import policy_for_source
        policy = policy_for_source(connection.authority.runner, connection.authority.sessions[ref.session_id].source)
        if policy is None:
            raise RuntimeStoreError('permission_denied')
        return {'value': policy.cwd}

    from functools import partial
    from gateway.session_controls import AuthorityConnection
    original = AuthorityConnection.handlers

    def handlers(self):
        table = original(self)
        table['future.read'] = partial(future_handler, 'session:read', self)
        table['future.write'] = partial(future_handler, 'session:control', self)
        return table
    monkeypatch.setattr(AuthorityConnection, 'handlers', handlers)
    from hermes_state_runtime import RuntimeStoreError

    world = await _bound(tmp_path)
    await _open(world)
    assert await _outcome(world, ran_here, 'futurecmd x') == 'relayed'
    assert await _outcome(world, ran_here, 'futureplugin go') == 'ran'  # plugin commands answer here
    read = await _call(world, 'future.read')
    assert read.get('result') == {'value': str(tmp_path)}, read  # a read answers here
    write = await _call(world, 'future.write')
    assert _reason(write) == 'lane_owned', write  # a write: the TUI relays the typed command


def _synthesized(contract, sid):
    """Minimal valid params for *contract*: every required field filled by its type."""
    import typing
    params = {}
    for name, field in contract.params.model_fields.items():
        if name == 'session_id':
            params[name] = sid
            continue
        if not field.is_required():
            continue
        annotation = field.annotation
        origin = typing.get_origin(annotation)
        if origin is typing.Literal:
            params[name] = typing.get_args(annotation)[0]
        elif annotation is str:
            params[name] = 'x'
        elif annotation is bool:
            params[name] = False
        elif annotation is int:
            params[name] = 0
        elif annotation is float:
            params[name] = 0.0
        elif annotation is dict or origin is dict:
            params[name] = {}
        elif annotation is list or origin is list:
            params[name] = []
        else:
            return None  # a nested model: cannot be synthesized here
    return params


# Session-scoped canonical methods whose params this sweep cannot synthesize, or whose call would end
# the session under the rest of the sweep (each has its own tests).
_UNSYNTHESIZED = frozenset({'session.delete', 'session.detach'})
# Required params that are nested models: called by their own tests, not synthesized here.
_NESTED = frozenset({'message.react'})


def _contract(name):
    from tui_gateway.contracts.registry import CANONICAL_METHODS, METHODS
    return CANONICAL_METHODS.get(name) or METHODS.get(name)


def _session_methods(served=None):
    """Every declared method taking a ``session_id``: all of CANONICAL_METHODS, plus each legacy
    METHODS entry the owner serves (*served*: ``AuthorityConnection.handlers()``)."""
    from tui_gateway.contracts.registry import CANONICAL_METHODS, METHODS
    names = set(CANONICAL_METHODS) | ({name for name in METHODS if name in served} if served else set())
    return sorted(name for name in names
                  if 'session_id' in _contract(name).params.model_fields and name not in _UNSYNTHESIZED)


@pytest.mark.asyncio
async def test_no_canonical_session_method_answers_permission_denied_on_a_bound_session(tmp_path):
    """Every CANONICAL_METHODS entry (and every served legacy method) that takes a ``session_id``, with
    synthesized minimal params, from the bound session's owner (attached)."""
    world = await _bound(tmp_path)
    await _open(world)
    denied, skipped = {}, []
    methods = _session_methods(world.tui.handlers())
    assert len(methods) > 60, methods  # the sweep really covers the surface
    for name in methods:
        params = _synthesized(_contract(name), world.sid)
        if params is None:
            skipped.append(name)
            continue
        params.pop('session_id')
        reason, _exc = await _attempt(world, name, params)
        if reason == 'permission_denied':
            denied[name] = reason
        await _open(world)  # a refusal may not detach; a re-open keeps the next call attached
    assert not denied, denied
    assert set(skipped) <= _NESTED, skipped


# ── the negative side: chats the mirror did not bind keep every refusal ────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize('platform', ['discord', 'telegram'])
async def test_another_users_messaging_chat_stays_refused(tmp_path, ran_here, monkeypatch, platform):
    _plugins(monkeypatch)
    world = await _foreign(tmp_path, platform)
    await _open(world)  # reading a chat is allowed (the cross-surface viewer); acting on it is not
    for typed in ('memory pending', 'sandboxx off', 'title x'):
        assert await _outcome(world, ran_here, typed) == 'permission_denied', typed
    assert await _outcome(world, ran_here, 'compress') == 'unsupported_command'  # as for any session
    snap = {**await _open(world), '_file': str(tmp_path / 'none.txt')}
    for label in ('!shell', '/rollback (list), /undo preview', '/background', '/compress', '/undo, /retry, /rewind'):
        method, build, _relayed = TUI_RPCS[label]
        reason, _exc = await _attempt(world, method, build(snap))
        assert reason != 'lane_owned', (label, reason)
        assert reason is not None, label
    reply = await _call(world, 'prompt.submit', text='hi', submission_id='s-1')
    assert world.lane.forwarded == [] and ran_here == [], (world.lane.forwarded, ran_here, reply)


@pytest.mark.asyncio
async def test_a_bound_session_needs_the_owners_capability(tmp_path):
    """The seam answers by class only for an actor holding what the call asked for: a read-only
    socket (a shared Webapp tab) gets no write, relayed or otherwise."""
    from gateway.session_controls import AuthorityConnection
    world = await _bound(tmp_path)
    reader = AuthorityConnection(world.authority, object(), {
        'user_id': 'viewer', 'profile_id': 'fixture', 'instance_id': 'cold', 'capabilities': ['session:read']})
    world.tui = reader
    await _open(world)
    reply = await _call(world, 'shell.exec', command='echo hi')
    assert _reason(reply) == 'permission_denied', reply
    reply = await _call(world, 'slash.exec', command='sandbox off')
    assert _reason(reply) == 'permission_denied', reply
    assert world.lane.forwarded == []


# ── reads that degrade quietly instead of refusing ────────────────────────────────────────────
# A handler written for local sessions may answer "no launch policy" with an EMPTY result rather
# than a refusal (``complete.path``: the `@` file picker). The rescue seam never saw those; a read
# that met no policy on a bound session is now re-run with the viewer policy.


@pytest.mark.asyncio
async def test_at_file_picker_lists_the_bound_sessions_folder(tmp_path):
    (tmp_path / 'notes_alpha.txt').write_text('x')
    world = await _bound(tmp_path)
    await _open(world)
    reply = await _call(world, 'complete.path', word='notes')
    assert 'error' not in reply, reply
    texts = json.dumps(reply['result']['items'])
    assert 'notes_alpha.txt' in texts, reply  # was {'items': []} before: the picker showed nothing


@pytest.mark.asyncio
async def test_a_future_read_that_degrades_to_empty_gets_the_real_answer(tmp_path, monkeypatch):
    from functools import partial
    from tui_gateway.contracts import registry
    from tui_gateway.contracts.base import Params, Result
    from gateway.session_controls import AuthorityConnection

    class P(Params):
        session_id: str

    class R(Result):
        cwd: str

    monkeypatch.setitem(registry.CANONICAL_METHODS, 'future.quiet', registry.MethodContract('future.quiet', P, R))

    async def quiet(connection, ref, params):
        connection.authority.authorize(connection.actor, ref, 'session:read')
        from gateway.session_policy import policy_for_source
        policy = policy_for_source(connection.authority.runner, connection.authority.sessions[ref.session_id].source)
        return {'cwd': policy.cwd if policy is not None else ''}  # "advisory": empty, no refusal
    original = AuthorityConnection.handlers

    def handlers(self):
        table = original(self)
        table['future.quiet'] = partial(quiet, self)
        return table
    monkeypatch.setattr(AuthorityConnection, 'handlers', handlers)
    world = await _bound(tmp_path)
    await _open(world)
    reply = await _call(world, 'future.quiet')
    assert reply.get('result') == {'cwd': str(tmp_path)}, reply
