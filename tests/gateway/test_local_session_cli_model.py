"""A local session the shared gateway hosts (the Ink TUI, the CLI, desktop) runs the machine's CLI model.

Since the shared-gateway merge the TUI's sessions are created by the gateway. One created without a model
took ``_resolve_gateway_model``, which answers ``gateway.model`` (the phone route, C118), and ignored
``host.yaml`` (C121): the TUI ran on the phone's model (C150). Phone turns keep ``gateway.model``.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.route_config import local_session_config
from gateway.session_controls import AuthorityConnection

CFG = {
    'model': {'provider': 'anthropic', 'default': 'claude-sonnet-5-5'},
    'agent': {'reasoning_effort': 'medium'},
    'platform_toolsets': {'cli': [], 'tui': []},
    'gateway': {'model': {'provider': 'openai-codex', 'default': 'gpt-6-luna', 'reasoning_effort': 'medium'},
                'fallback_providers': [{'provider': 'deepseek', 'model': 'deepseek-v4.1-flash'}],
                'external_supervisor': 'nix'},
}
HOST = {'model': {'provider': 'zai', 'default': 'glm-5.3-flash'}, 'agent': {'reasoning_effort': 'high'}}


def test_a_local_session_config_drops_the_phone_route_and_takes_the_host_model():
    merged = local_session_config(CFG, HOST)
    assert merged['model'] == {'provider': 'zai', 'default': 'glm-5.3-flash'}
    assert merged['agent']['reasoning_effort'] == 'high'
    assert 'model' not in merged['gateway']
    assert merged['gateway']['external_supervisor'] == 'nix'
    assert CFG['gateway']['model']['default'] == 'gpt-6-luna', 'the shared config is not mutated'


def test_without_a_host_file_a_local_session_keeps_the_shared_model_and_phone_turns_keep_theirs():
    from gateway.run import _resolve_gateway_model
    assert _resolve_gateway_model(local_session_config(CFG, None)) == 'claude-sonnet-5-5'
    assert _resolve_gateway_model(CFG) == 'gpt-6-luna', 'messaging turns still route through gateway.model'


async def _authority(tmp_path, monkeypatch, host_yaml):
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway import run

    home = tmp_path / 'home'
    home.mkdir()
    if host_yaml is not None:
        (home / 'host.yaml').write_text(host_yaml, encoding='utf-8')
    monkeypatch.setattr(run, '_load_gateway_config', lambda: CFG)
    monkeypatch.setattr(run, '_gateway_config_home', lambda: home)
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = run.GatewayRunner.__new__(run.GatewayRunner)
    runner.adapters, runner.session_store, runner._session_db, runner._draining = {}, store, store._db, False
    authority = await initialize_session_authority(runner, profile_id=str(Path(store._db.db_path).parent), instance_id='test')
    authority._schedule = lambda ref: None
    return authority


@pytest.mark.asyncio
@pytest.mark.parametrize(('host_yaml', 'expected'), [
    ('model:\n  provider: anthropic\n  default: claude-sonnet-5-5\n', 'claude-sonnet-5-5'),
    ('model:\n  provider: zai\n  default: glm-5.3-flash\n', 'glm-5.3-flash'),
    (None, 'claude-sonnet-5-5'),
])
async def test_a_tui_session_created_without_a_model_runs_the_cli_model_not_the_phone_model(
        tmp_path, monkeypatch, host_yaml, expected):
    authority = await _authority(tmp_path, monkeypatch, host_yaml)
    conn = AuthorityConnection(authority, SimpleNamespace(write=lambda frame: None), {'user_id': 'owner'})
    cwd = str(Path(authority.db.db_path).parent)
    created = await conn.dispatch({'id': 1, 'method': 'session.create',
                                   'params': {'request_id': 'tui', 'source': 'tui', 'cwd': cwd}})
    assert 'result' in created, created
    assert authority.db.get_session(created['result']['stored_session_id'])['model'] == expected
    await conn.close()
    authority.db.close()
