#!/usr/bin/env python3
"""Show which model and effort a session will run, and who decided it. Read-only, stdlib only.

Run it after picking a model, and again after every gateway restart, resume or reconnect.

    scripts/session_model.py <session-id | id-prefix | title-substring>
    scripts/session_model.py "Superset dashboard"
    scripts/session_model.py local-0585ce77 --expect claude-opus-5-5 --effort medium
    scripts/session_model.py "Superset dashboard" --json

Options:
    --home DIR     the Hermes home (default: $HERMES_HOME, else ~/.hermes)
    --expect M     exit 1 unless the decided model is M (a prefix such as claude-opus works)
    --effort E     with --expect: the decided effort must also be E
    --json         machine-readable output (one object per match)

What it prints, per matching session:

  decided       the model and effort the session's NEXT turn runs, and the source that decided it:
                  lane override  the routing entry's model_override (what a Discord/Telegram thread's next turn
                                 resolves; written by a pick on a thread-owned session), with its effort from
                                 the entry's lane_reasoning pick, else the carried level, else config
                  receipt        the newest model/reasoning pick receipt (a local session's frozen launch
                                 policy; for a thread-owned session the receipt is only the record of the pick)
                  config default gateway.model (a thread) or host.yaml / model (a local session), because no
                                 pick is recorded: this is what a restart falls back to
  receipt       the newest gateway.mutation.v1 model pick and the newest reasoning pick of the conversation
                (revision = the session's runtime_revision when it was committed)
  routing       the thread's routing entry: model_override, lane_reasoning, carried route
  usage         the 5 newest session_model_usage rows (model, provider, calls, first and last call time). The
                table is kept per model, not per call, so these are the last 5 DISTINCT models the session called
                and when each was last used; the newest assistant message time is shown beside it.
  verdict       lines starting with WARN when the receipt, the routing entry and the models that actually ran
                disagree (for example the decided model never ran, or a fallback ran)

Success after a pick or a restart: `decided` names the model and effort you picked, its source is `lane override`
(thread-owned session) or `receipt` (local session), no WARN lines, and `usage` shows the picked model as the
newest row once a turn has run on it.

Never writes: the database is opened with mode=ro and query_only; no file is created or touched.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Optional

RECEIPT_PREFIX = 'gateway.mutation.v1.'
MAX_MATCHES = 5
USAGE_ROWS = 5


# -- reading -----------------------------------------------------------------------------------------------


def connect(db_path: Path) -> sqlite3.Connection:
    """The state database, read-only (a WAL database still shows its newest commits)."""
    conn = sqlite3.connect(f'{db_path.resolve().as_uri()}?mode=ro', uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    return conn


def _loads(raw: Any) -> dict:
    try:
        value = json.loads(raw) if isinstance(raw, (str, bytes)) and raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _scalar(text: str) -> Any:
    text = text.strip()
    if text[:1] in ('"', "'") and text[-1:] == text[:1] and len(text) > 1:
        return text[1:-1]
    text = re.sub(r'\s+#.*$', '', text).strip()
    return {'true': True, 'false': False, 'null': None, '~': None, '': None}.get(text.lower(), text)


def read_yaml_maps(path: Path) -> dict:
    """The nested ``key: scalar`` maps of a YAML file (lists and anchors are skipped): enough for the
    model blocks and agent.reasoning_effort. An unreadable file reads as empty."""
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except OSError:
        return {}
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for line in lines:
        if not line.strip() or line.lstrip().startswith(('#', '-')):
            continue
        indent = len(line) - len(line.lstrip(' '))
        key, sep, rest = line.strip().partition(':')
        if not sep:
            continue
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else root
        key = key.strip().strip('"\'')
        if rest.strip() and not rest.strip().startswith('#'):
            parent[key] = _scalar(rest)
        else:
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
    return root


def _get(tree: dict, *path: str) -> Any:
    for part in path:
        tree = tree.get(part) if isinstance(tree, dict) else None
    return tree


def config_defaults(home: Path) -> dict:
    """The models a session falls back to with no pick: ``gateway`` (a thread) and ``local`` (host.yaml, else
    config.yaml's ``model``), each ``{model, provider, effort}``."""
    config, host = read_yaml_maps(home / 'config.yaml'), read_yaml_maps(home / 'host.yaml')

    def block(tree, *path):
        found = _get(tree, *path)
        return found if isinstance(found, dict) else {}
    gateway = block(config, 'gateway', 'model')
    cli = block(host, 'model') or block(config, 'model')
    return {
        # A thread turn on the gateway block's own model takes the block's effort; any other model takes
        # agent.reasoning_effort (``_load_reasoning_config``), so ``effort_other`` is what a picked model gets.
        'gateway': {'model': gateway.get('default') or cli.get('default'),
                    'provider': gateway.get('provider') or cli.get('provider'),
                    'effort': gateway.get('reasoning_effort') or _get(config, 'agent', 'reasoning_effort'),
                    'effort_other': _get(config, 'agent', 'reasoning_effort')},
        'local': {'model': cli.get('default'), 'provider': cli.get('provider'),
                  'effort': _get(host, 'agent', 'reasoning_effort') or _get(config, 'agent', 'reasoning_effort')},
    }


def find_sessions(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    """Rows whose id equals or starts with *query*, else whose title contains it (newest activity first)."""
    cols = 'id, title, source, created_source, model, session_key, parent_session_id, started_at, ended_at, ' \
           'end_reason, runtime_revision, last_activity_at'
    exact = conn.execute(f'SELECT {cols} FROM sessions WHERE id = ?', (query,)).fetchall()
    if exact:
        return exact
    like = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    by_id = conn.execute(f"SELECT {cols} FROM sessions WHERE id LIKE ? ESCAPE '\\' "
                         'ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT ?', (like + '%', MAX_MATCHES + 1)).fetchall()
    if by_id:
        return by_id
    return conn.execute(f"SELECT {cols} FROM sessions WHERE title LIKE ? ESCAPE '\\' "
                        'ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT ?',
                        ('%' + like + '%', MAX_MATCHES + 1)).fetchall()


def lineage(conn: sqlite3.Connection, session_id: str, limit: int = 400) -> list[str]:
    """*session_id* with its ancestors and descendants through ``parent_session_id`` (a compression chain)."""
    seen, queue = [session_id], [session_id]
    while queue and len(seen) < limit:
        current = queue.pop()
        up = conn.execute('SELECT parent_session_id FROM sessions WHERE id = ?', (current,)).fetchone()
        down = conn.execute('SELECT id FROM sessions WHERE parent_session_id = ?', (current,)).fetchall()
        for found in ([up[0]] if up and up[0] else []) + [row[0] for row in down]:
            if found not in seen:
                seen.append(found)
                queue.append(found)
    return seen


def receipts(conn: sqlite3.Connection, ids: list[str]) -> dict:
    """The newest model pick and the newest reasoning pick recorded for any session in *ids*."""
    wanted, newest = set(ids), {'model': None, 'reasoning': None}
    for key, value in conn.execute('SELECT key, value FROM state_meta WHERE key LIKE ?', (RECEIPT_PREFIX + '%',)):
        result = _loads(value).get('result')
        if not isinstance(result, dict) or result.get('session_id') not in wanted:
            continue
        operation = result.get('operation')
        if operation in newest and isinstance(result.get('revision'), int):
            held = newest[operation]
            if held is None or result['revision'] > held['revision']:
                newest[operation] = {k: result.get(k) for k in ('model', 'provider', 'effort', 'revision', 'session_id')
                                     if result.get(k) is not None}
    return newest


def carried_record(conn: sqlite3.Connection, ids: list[str]) -> dict:
    """The route a session's hand-off to a thread recorded and the thread has not adopted yet (consumed at
    the thread's first turn): ``{model, provider, reasoning}`` or empty."""
    for sid in ids:
        row = conn.execute('SELECT value FROM state_meta WHERE key = ?', ('gateway.lane_model.v1:' + sid,)).fetchone()
        if row:
            return _loads(row[0])
    return {}


def routing_entry(conn: sqlite3.Connection, row: sqlite3.Row, ids: list[str], home: Path) -> Optional[dict]:
    """The thread's routing entry: the gateway_routing table (primary copy), else sessions.json (its mirror)."""
    wanted = set(ids)
    candidates: list[dict] = []
    try:
        for found in conn.execute('SELECT session_key, entry_json FROM gateway_routing'):
            entry = _loads(found['entry_json'])
            if entry.get('session_id') in wanted or found['session_key'] == row['session_key']:
                candidates.append({**entry, 'session_key': found['session_key'], '_from': 'gateway_routing'})
    except sqlite3.Error:
        pass
    if not candidates:
        mirror = _loads((home / 'sessions' / 'sessions.json').read_text(encoding='utf-8')
                        if (home / 'sessions' / 'sessions.json').exists() else '')
        for key, entry in mirror.items():
            if isinstance(entry, dict) and (entry.get('session_id') in wanted or key == row['session_key']):
                candidates.append({**entry, 'session_key': key, '_from': 'sessions.json'})
    # The conversation's own thread first (the row's session key), then any messaging lane, then a local route.
    def rank(c):
        return (c['session_key'] != row['session_key'], (c.get('platform') or '') in ('', 'local'),
                c.get('session_id') not in wanted)
    return min(candidates, key=rank) if candidates else None


def usage(conn: sqlite3.Connection, ids: list[str]) -> tuple[list[dict], Optional[float]]:
    """The newest ``USAGE_ROWS`` per-model usage rows of *ids* and the newest assistant message time."""
    marks = ','.join('?' * len(ids))
    rows = [dict(r) for r in conn.execute(
        f'SELECT model, billing_provider AS provider, SUM(api_call_count) AS calls, MIN(first_seen) AS first_seen, '
        f'MAX(last_seen) AS last_seen FROM session_model_usage WHERE session_id IN ({marks}) '
        f'GROUP BY model, billing_provider ORDER BY MAX(last_seen) DESC LIMIT ?', (*ids, USAGE_ROWS))]
    newest = conn.execute(f"SELECT MAX(timestamp) FROM messages WHERE role = 'assistant' AND session_id IN ({marks})",
                          ids).fetchone()
    return rows, (newest[0] if newest else None)


# -- deciding ----------------------------------------------------------------------------------------------


def _effort_of(config: Any) -> Optional[str]:
    if not isinstance(config, dict):
        return None
    return 'none' if config.get('enabled') is False else (str(config['effort']) if config.get('effort') else None)


def decide(row: sqlite3.Row, entry: Optional[dict], picks: dict, defaults: dict, pending: Optional[dict] = None) -> dict:
    """``{model, provider, effort, model_source, effort_source}`` the session's next turn runs."""
    metadata = entry.get('metadata') if entry and isinstance(entry.get('metadata'), dict) else {}
    override = entry.get('model_override') if entry and isinstance(entry.get('model_override'), dict) else {}
    lane = bool(entry) and (entry.get('platform') or '') not in ('', 'local')
    carried = metadata.get('lane_model_carried') if isinstance(metadata.get('lane_model_carried'), dict) else {}
    carried = carried or (pending or {})  # a hand-off record the thread's first turn will adopt
    model_pick = picks.get('model')
    receipt_effort = newest_effort(picks)
    base = defaults['gateway' if lane else 'local']
    decided: dict = {}
    if lane and override.get('model'):
        decided.update(model=override['model'], provider=override.get('provider'), model_source='lane override')
    elif model_pick and not lane:
        decided.update(model=model_pick.get('model'), provider=model_pick.get('provider'), model_source='receipt')
    elif lane and carried.get('model'):
        decided.update(model=carried['model'], provider=carried.get('provider'), model_source='lane carried route')
    else:
        decided.update(model=base.get('model'), provider=base.get('provider'), model_source='config default')
    lane_effort = _effort_of(metadata.get('lane_reasoning'))
    if lane and lane_effort:
        decided.update(effort=lane_effort, effort_source='lane override')
    elif not lane and receipt_effort:
        decided.update(effort=receipt_effort, effort_source='receipt')
    elif lane and _effort_of(carried.get('reasoning')):
        decided.update(effort=_effort_of(carried['reasoning']), effort_source='lane carried route')
    else:
        on_block_model = decided['model'] in (None, base.get('model'))
        decided.update(effort=base.get('effort') if on_block_model or not lane else base.get('effort_other') or base.get('effort'),
                       effort_source='config default')
    return decided


def newest_effort(picks: dict) -> Optional[str]:
    """The effort of the newest pick that named one: a model pick carries its level, a reasoning pick is the level."""
    named = [p for p in (picks.get('model'), picks.get('reasoning')) if p and p.get('effort')]
    return max(named, key=lambda p: p['revision'])['effort'] if named else None


def verdicts(decided: dict, picks: dict, entry: Optional[dict], recent: list[dict], lane: bool) -> list[str]:
    lines = []
    model_pick = picks.get('model')
    override = (entry or {}).get('model_override') or {}
    if lane and model_pick and override.get('model') and model_pick.get('model') != override['model']:
        lines.append(f"WARN receipt says {model_pick.get('model')} (revision {model_pick.get('revision')}) but the "
                     f"thread's next turn runs {override['model']}: a later pick, a /resume, or a revert")
    if lane and model_pick and not override.get('model'):
        lines.append(f"WARN a model pick is recorded ({model_pick.get('model')}) but the thread has no saved override: "
                     f"the next turn runs the {decided['model_source']} ({decided['model']})")
    picked_effort = newest_effort(picks)
    if lane and picked_effort and decided.get('effort') != picked_effort:
        lines.append(f"WARN the last effort pick was {picked_effort} but the next turn runs "
                     f"{decided.get('effort')} ({decided['effort_source']}): a restart would not keep the pick")
    if recent and decided.get('model') and not str(recent[0]['model']).startswith(str(decided['model'])):
        lines.append(f"WARN the newest model that actually ran is {recent[0]['model']}, not {decided['model']}: no turn "
                     "has run since the pick, or a fallback ran")
    return lines


def _when(stamp: Optional[float]) -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stamp)) if stamp else '-'


def inspect(conn: sqlite3.Connection, row: sqlite3.Row, home: Path, defaults: dict) -> dict:
    ids = lineage(conn, row['id'])
    entry = routing_entry(conn, row, ids, home)
    picks = receipts(conn, ids)
    recent, newest_message = usage(conn, ids)
    decided = decide(row, entry, picks, defaults, carried_record(conn, ids))
    lane = bool(entry) and (entry.get('platform') or '') not in ('', 'local')
    metadata = (entry or {}).get('metadata') or {}
    return {
        'session': {k: row[k] for k in ('id', 'title', 'source', 'created_source', 'model', 'session_key', 'runtime_revision')},
        'conversation_rows': len(ids),
        'decided': decided,
        'receipt': picks,
        'routing': None if entry is None else {
            'from': entry['_from'], 'session_key': entry.get('session_key'), 'session_id': entry.get('session_id'),
            'platform': entry.get('platform'), 'model_override': entry.get('model_override'),
            'lane_reasoning': metadata.get('lane_reasoning'), 'carried': metadata.get('lane_model_carried')},
        'usage': [{**r, 'first_seen': _when(r['first_seen']), 'last_seen': _when(r['last_seen'])} for r in recent],
        'newest_assistant_message': _when(newest_message),
        'config_default': defaults['gateway' if lane else 'local'],
        'verdict': verdicts(decided, picks, entry, recent, lane),
    }


# -- output ------------------------------------------------------------------------------------------------


def render(report: dict) -> str:
    session, decided = report['session'], report['decided']
    out = [f"session   {session['id']}", f"title     {session['title'] or '-'}",
           f"source    {session['source']} (created by {session['created_source'] or '-'}); row model {session['model'] or '-'}; "
           f"{report['conversation_rows']} row(s) in the conversation",
           f"decided   model {decided['model'] or '-'} / provider {decided['provider'] or '-'}  [{decided['model_source']}]",
           f"          effort {decided['effort'] or '-'}  [{decided['effort_source']}]"]
    picks = report['receipt']
    for name in ('model', 'reasoning'):
        pick = picks[name]
        shown = ', '.join(f'{k}={v}' for k, v in pick.items()) if pick else 'none recorded'
        out.append(f"receipt   {name:<9} {shown}")
    routing = report['routing']
    if routing is None:
        out.append('routing   no routing entry (a local session: its launch policy and receipt decide)')
    else:
        override = routing['model_override']
        out.append(f"routing   {routing['platform']} thread {routing['session_key']} ({routing['from']})")
        out.append(f"          model_override {json.dumps(override) if override else 'none'}; "
                   f"lane_reasoning {json.dumps(routing['lane_reasoning']) if routing['lane_reasoning'] else 'none'}")
    default = report['config_default']
    out.append(f"config    would run {default['model']} / {default['provider']} / effort {default['effort']} with no pick")
    out.append(f"usage     newest assistant message {report['newest_assistant_message']}; newest {USAGE_ROWS} models used:")
    for r in report['usage'] or []:
        out.append(f"          {r['last_seen']}  {r['model']} ({r['provider'] or '-'})  {r['calls']} call(s), first {r['first_seen']}")
    if not report['usage']:
        out.append('          no usage rows')
    out.extend(report['verdict'] or ['verdict   consistent: nothing disagrees'])
    return '\n'.join(out)


def check(report: dict, expect: Optional[str], effort: Optional[str]) -> bool:
    """Whether the decided model (a prefix is enough) and, when given, effort are the expected ones."""
    if not expect:
        return True
    decided = report['decided']
    return str(decided['model'] or '').startswith(expect) and (effort is None or decided['effort'] == effort)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='Which model and effort a session will run, and who decided it.')
    parser.add_argument('query', help='a session id, an id prefix, or part of its title')
    parser.add_argument('--home', default=os.environ.get('HERMES_HOME') or str(Path.home() / '.hermes'))
    parser.add_argument('--expect', help='exit 1 unless the decided model starts with this')
    parser.add_argument('--effort', help='with --expect: the decided effort must equal this')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    home = Path(args.home).expanduser()
    db_path = home / 'state.db'
    if not db_path.exists():
        print(f'no state.db under {home}', file=sys.stderr)
        return 2
    conn = connect(db_path)
    try:
        rows = find_sessions(conn, args.query)
        if not rows:
            print(f'no session matches {args.query!r}', file=sys.stderr)
            return 2
        defaults = config_defaults(home)
        reports = [inspect(conn, row, home, defaults) for row in rows[:MAX_MATCHES]]
    finally:
        conn.close()
    verdict = all(check(r, args.expect, args.effort) for r in reports)
    if args.json:
        print(json.dumps(reports if len(reports) > 1 else reports[0], indent=2, default=str))
    else:
        print('\n\n'.join(render(r) for r in reports))
        if len(rows) > MAX_MATCHES:
            print(f'\n(more than {MAX_MATCHES} matches: narrow the query)')
        if args.expect:
            wanted = args.expect + (f' / {args.effort}' if args.effort else '')
            print(f"\n{'PASS' if verdict else 'FAIL'}: expected {wanted}")
    return 0 if verdict else 1


if __name__ == '__main__':
    sys.exit(main())
