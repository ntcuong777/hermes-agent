"""``scripts/session_model.py``: the read-only check a user runs after a pick and after each restart.

Its input is the state the real gateway writes, so the main tests produce it with the real authority (a pick
on a mirror-bound session, a restart) and read it with the script; a few hand-built rows cover what a gateway
of an older build left (a receipt with no saved effort) and the failure messages.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from tests.gateway.test_lane_model_survives import (
    PICK, _bound, _pick, _restart, _stop)

pytestmark = pytest.mark.usefixtures('authority_cwd_in_tmp')

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'session_model.py'


def _module():
    spec = importlib.util.spec_from_file_location('session_model_script', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(home, *argv):
    module = _module()
    return module, module.main([*argv, '--home', str(home)])


def _report(home, query, capsys):
    _module, code = _run(home, query, '--json')
    out = capsys.readouterr().out
    return code, json.loads(out)


def _home(world):
    return Path(world.authority.db.db_path).parent


@pytest.mark.asyncio
async def test_after_a_pick_it_names_the_model_and_effort_and_the_lane_as_the_source(tmp_path, monkeypatch, capsys):
    world = await _bound(tmp_path, monkeypatch)
    await _pick(world)
    await _stop(world)
    code, report = _report(_home(world), world.sid[:12], capsys)
    assert code == 0
    assert (report['decided']['model'], report['decided']['provider'], report['decided']['effort']) == PICK
    assert (report['decided']['model_source'], report['decided']['effort_source']) == ('lane override',) * 2
    assert report['receipt']['model']['model'] == PICK[0] and report['receipt']['model']['revision'] >= 1
    assert report['routing']['model_override']['model'] == PICK[0]
    assert report['routing']['lane_reasoning']['effort'] == PICK[2]
    assert all(v.startswith('WARN the newest model that actually ran') for v in report['verdict'])
    assert world.route == report['routing']['session_key']


@pytest.mark.asyncio
async def test_it_still_says_so_after_a_restart_and_expect_passes_or_fails_by_exit_code(tmp_path, monkeypatch, capsys):
    world = await _bound(tmp_path, monkeypatch)
    await _pick(world)
    cold = await _restart(world, monkeypatch)
    await _stop(cold)
    _module, code = _run(_home(cold), world.sid, '--expect', 'claude-opus', '--effort', PICK[2])
    assert code == 0 and 'PASS: expected claude-opus / low' in capsys.readouterr().out
    _module, code = _run(_home(cold), world.sid, '--expect', 'claude-opus', '--effort', 'high')
    assert code == 1 and 'FAIL' in capsys.readouterr().out
    _module, code = _run(_home(cold), world.sid, '--expect', 'gpt-6')
    assert code == 1


@pytest.mark.asyncio
async def test_a_session_found_by_title_and_one_never_picked_report_the_defaults(tmp_path, monkeypatch, capsys):
    world = await _bound(tmp_path, monkeypatch)
    await _stop(world)
    home = _home(world)
    (home / 'config.yaml').write_text('model:\n  provider: anthropic\n  default: claude-sonnet-5-5\n'
                                      'agent:\n  reasoning_effort: high\n'
                                      'gateway:\n  model:\n    provider: openai-codex\n    default: gpt-6-luna\n'
                                      '    reasoning_effort: medium\n', encoding='utf-8')
    with sqlite3.connect(world.authority.db.db_path) as conn:
        conn.execute("UPDATE sessions SET title='Automate daily triage' WHERE id=?", (world.sid,))
    code, report = _report(home, 'DAILY TRIAGE', capsys)
    assert code == 0 and report['session']['id'] == world.sid
    assert report['decided']['model_source'] == 'lane carried route', 'the lane has not adopted a pick yet'
    assert report['receipt'] == {'model': None, 'reasoning': None}
    assert report['config_default']['model'] == 'gpt-6-luna'


def test_a_receipt_the_lane_never_applied_is_called_out(tmp_path, capsys):
    """What a gateway of the earlier build left: the effort pick is in a receipt only."""
    db = tmp_path / 'state.db'
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, source TEXT, created_source TEXT, model TEXT,
                session_key TEXT, parent_session_id TEXT, started_at REAL, ended_at REAL, end_reason TEXT,
                runtime_revision INTEGER, last_activity_at REAL);
            CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE gateway_routing (scope TEXT, session_key TEXT, entry_json TEXT, updated_at REAL);
            CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, api_call_count INT,
                first_seen REAL, last_seen REAL);
            CREATE TABLE messages (session_id TEXT, role TEXT, timestamp REAL);
        """)
        conn.execute("INSERT INTO sessions VALUES ('s1','T','discord','tui','claude-opus-5-5','k',NULL,1,NULL,NULL,5,2)")
        conn.execute("INSERT INTO state_meta VALUES ('gateway.mutation.v1.a', ?)", (json.dumps({'result': {
            'session_id': 's1', 'operation': 'model', 'model': 'claude-opus-5-5', 'provider': 'anthropic',
            'effort': 'medium', 'revision': 5}}),))
        conn.execute("INSERT INTO gateway_routing VALUES ('', 'k', ?, 1)", (json.dumps({
            'session_id': 's1', 'platform': 'discord',
            'model_override': {'model': 'claude-opus-5-5', 'provider': 'anthropic'}, 'metadata': {}}),))
        conn.execute("INSERT INTO session_model_usage VALUES ('s1','claude-sonnet-5-5','anthropic',9,1,50)")
    (tmp_path / 'config.yaml').write_text('agent:\n  reasoning_effort: high\n', encoding='utf-8')
    code, report = _report(tmp_path, 's1', capsys)
    assert code == 0
    assert report['decided']['effort'] == 'high' and report['decided']['effort_source'] == 'config default'
    warnings = ' '.join(report['verdict'])
    assert 'a restart would not keep the pick' in warnings
    assert 'newest model that actually ran is claude-sonnet-5-5' in warnings


def test_it_never_writes_and_reports_a_missing_session_or_state(tmp_path, capsys):
    module, code = _run(tmp_path / 'nowhere', 'x')
    assert code == 2 and 'no state.db' in capsys.readouterr().err
    tmp_path = tmp_path / 'home'
    tmp_path.mkdir()
    db = tmp_path / 'state.db'
    sqlite3.connect(db).executescript('CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, source TEXT, '
                                      'created_source TEXT, model TEXT, session_key TEXT, parent_session_id TEXT, '
                                      'started_at REAL, ended_at REAL, end_reason TEXT, runtime_revision INT, '
                                      'last_activity_at REAL);').close()
    before = db.read_bytes()
    _module, code = _run(tmp_path, 'no-such-session')
    assert code == 2 and 'no session matches' in capsys.readouterr().err
    assert db.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['state.db'], 'no journal or side file was created'
    with pytest.raises(sqlite3.OperationalError):
        module.connect(db).execute('CREATE TABLE x (a)')
