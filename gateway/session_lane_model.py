"""A local conversation a messaging lane takes over keeps the model it ran.

A local session (TUI, CLI, Desktop) runs its frozen launch policy's route. A lane move (the Discord
session mirror's ``/resume <sid> --all``, ``/handoff``, a platform ``/resume``) releases that policy
(``hermes_state_local.hand_off_conversations``), and a lane turn resolves its route from the chat's
own ``/model`` pick, else the profile default: the conversation silently switched models.

The move records the policy's effective route under ``CARRY_PREFIX + <row id>`` in the same write
that releases it. The lane's first resolution adopts it as the chat's ``/model`` pick (the persisted
``SessionEntry.model_override`` a typed ``/model`` writes) plus a carried reasoning level, unless the
chat already has a pick. A row a lane took over before this record existed carries no record; its
creating surface (``created_source``) and recorded route (``sessions.model`` + ``model_config``)
stand in. A conversation created on a messaging platform never has either, so its route stays the
profile's. Adoption runs once per lane conversation (``CARRIED`` in the entry's metadata; a
``/resume`` or ``/new`` starts a fresh entry), so a later ``/model`` change or reset stays the user's.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

CARRY_PREFIX = 'gateway.lane_model.v1:'
CARRIED = 'lane_model_carried'
# ``created_source`` of a conversation created by a local surface (``session_policy.created_surface``).
LOCAL_CREATED = frozenset({'cli', 'tui', 'desktop', 'acp', 'tool', 'oneshot'})
_ROUTE_KEYS = ('model', 'provider', 'base_url', 'api_mode')


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ''


def _loads(raw: Any) -> dict:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _route(model: str, provider: str, base_url: str, api_mode: str, reasoning: Any) -> Optional[dict]:
    """A carried route, or None when it holds nothing a lane could adopt. A model without a concrete
    provider is dropped: layered on the default provider it would reach the wrong endpoint."""
    picked = bool(model and provider and provider.lower() != 'auto')
    route = {
        **({'model': model, 'provider': provider, 'base_url': base_url, 'api_mode': api_mode} if picked else {}),
        'reasoning': dict(reasoning) if isinstance(reasoning, dict) else None,
    }
    return route if picked or route['reasoning'] is not None else None


def route_from_policy(policy: Mapping[str, Any]) -> Optional[dict]:
    """The route a local launch policy's next turn runs, from its frozen config alone. A session that
    follows its profile without a pick of its own runs the profile's model, as the lane will: only an
    explicit reasoning level carries then."""
    from gateway.session_policy import FOLLOW_ADOPTED, session_pinned
    from hermes_constants import resolve_reasoning_config
    config, request = _loads(policy.get('config_json')), _loads(policy.get('request_json'))
    block = config.get('model') if isinstance(config.get('model'), dict) else {}
    follows = request.get('follow_profile_config') is True or config.get(FOLLOW_ADOPTED) is True
    picked = not follows or block.get('selection_profile') is not None
    model = _text(policy.get('model')) or _text(block.get('default'))
    reasoning = (resolve_reasoning_config(config, model)
                 if not follows or session_pinned(config, 'reasoning', 'reasoning' in request) else None)
    return _route(model if picked else '', _text(block.get('provider')) if picked else '',
                  _text(block.get('base_url')), _text(block.get('api_mode')), reasoning)


def route_from_row(row: Mapping[str, Any]) -> Optional[dict]:
    """The route a session row records: ``sessions.model`` with the provider the gateway synced beside
    it (``model_config.gateway_runtime``), else the top-level route the TUI/Desktop write."""
    config = _loads(row.get('model_config'))
    runtime = config.get('gateway_runtime') if isinstance(config.get('gateway_runtime'), dict) else {}
    shape = runtime if _text(runtime.get('provider')) else config
    return _route(_text(row.get('model')), _text(shape.get('provider')), _text(shape.get('base_url')),
                  _text(shape.get('api_mode')), config.get('reasoning_config'))


def record_carried_route(conn, target: str, policy: Mapping[str, Any]) -> None:
    """Inside the lane move's write: remember *policy*'s route for row *target* before it is released.
    A policy it cannot read records nothing (the row's recorded route stands in); never fails the move."""
    try:
        route = route_from_policy(policy)
    except Exception:
        logger.warning('Could not read the local route handed off with row %s', target, exc_info=True)
        return
    if route is not None:
        conn.execute('INSERT OR REPLACE INTO state_meta(key,value) VALUES(?,?)',
                     (CARRY_PREFIX + target, json.dumps(route, sort_keys=True)))


def carried_route(db, session_id: str) -> Optional[dict]:
    """The route a lane conversation (row *session_id*, a messaging lane's) carries from a local
    surface: the move's record for it or a compression ancestor, else the newest locally created
    row's recorded route. None for a conversation that never was local."""
    from gateway.session_route_recovery import _messaging_platform
    from hermes_state_gateway import _COMPRESSION_LINEAGE_CTE
    with db._read_ctx() as conn:
        ids = [row[0] for row in conn.execute(
            f'{_COMPRESSION_LINEAGE_CTE} SELECT id FROM compression_lineage', (session_id,))]
        encoded = json.dumps(ids)
        rows = [dict(zip(('id', 'source', 'created_source', 'model', 'model_config', 'started_at'), row))
                for row in conn.execute(
                    'SELECT id, source, created_source, model, model_config, started_at FROM sessions '
                    'WHERE id IN (SELECT value FROM json_each(?))', (encoded,))]
        records = {key[len(CARRY_PREFIX):]: value for key, value in conn.execute(
            'SELECT key, value FROM state_meta WHERE key IN '
            '(SELECT ? || value FROM json_each(?))', (CARRY_PREFIX, encoded))}
    tip = next((row for row in rows if row['id'] == session_id), None)
    if tip is None or _messaging_platform(tip['source']) is None:
        return None
    recorded = next((_loads(records[sid]) for sid in ids if sid in records), None)
    if recorded:
        return _route(*(_text(recorded.get(key)) for key in _ROUTE_KEYS), recorded.get('reasoning'))
    local = sorted((row for row in rows if row['created_source'] in LOCAL_CREATED),
                   key=lambda row: row['started_at'] or 0, reverse=True)
    return next(filter(None, map(route_from_row, local)), None)


def consume_carried_route(db, session_id: str) -> None:
    """Delete the hand-off records of *session_id*'s lineage (see ``record_carried_route``)."""
    with db._read_ctx() as conn:
        ids = [row[0] for row in conn.execute(
            f'{_lineage_cte()} SELECT id FROM compression_lineage', (session_id,))]
    keys = [CARRY_PREFIX + sid for sid in ids]
    if keys:
        db._execute_write(lambda conn: conn.executemany('DELETE FROM state_meta WHERE key=?', [(k,) for k in keys]))


def _lineage_cte() -> str:
    from hermes_state_gateway import _COMPRESSION_LINEAGE_CTE
    return _COMPRESSION_LINEAGE_CTE


def adopt_carried_model(runner, session_key: Optional[str]) -> Optional[dict]:
    """The carried route of lane *session_key*, adopted on first use as its persisted ``/model`` pick.

    Returns the adopted record (also after a restart), or None: no lane, no SQLite, or a conversation
    that never was local (cached per conversation in memory, so an ordinary chat costs one read per
    process and gets no write). A chat that picked its own model first keeps it and adopts only the
    carried reasoning level. A provider that no longer resolves falls back on the turn's existing
    unavailable-override path (one warning, the default route). Never raises."""
    store = getattr(runner, 'session_store', None)
    db = getattr(store, '_db', None)
    if not session_key or store is None or db is None:
        return None
    try:
        # One adoption at a time: the session.info worker and the turn thread both land here, and a caller that
        # found the check "in progress" used to return None and run the turn on the profile's default.
        with runner.__dict__.setdefault('_lane_model_lock', threading.RLock()):
            marked = store.get_session_metadata(session_key, CARRIED)
            if isinstance(marked, dict):
                return marked
            entry = store.lookup_by_session_key(session_key)
            checked = runner.__dict__.setdefault('_lane_model_checked', set())
            if entry is None or (session_key, entry.session_id) in checked:
                return None
            checked.add((session_key, entry.session_id))
            route = carried_route(db, entry.session_id)
            if route is None:
                return None
            if runner._session_model_override(session_key) is not None or store.get_model_override(session_key):
                # The chat's own pick came first and stays (a later reset gives the profile's, not this).
                route = {'reasoning': route.get('reasoning')}
            elif route.get('model'):
                store.set_model_override(session_key, {key: route[key] for key in ('model', 'provider', 'base_url')})
            store.set_session_metadata(session_key, CARRIED, route)
            # Consumed: the lane's own picks and turns own the route from here. A later resume of this
            # conversation into another thread resolves what the row last ran, not this stale record.
            consume_carried_route(db, entry.session_id)
    except Exception:
        logger.warning('Could not carry the local model of lane session=%s; using its default route',
                       session_key, exc_info=True)
        return None
    logger.info('Lane session=%s keeps the local route it ran: model=%s provider=%s reasoning=%s',
                session_key, route.get('model') or '-', route.get('provider') or '-', route.get('reasoning'))
    return route


def carried_reasoning(runner, session_key: Optional[str], model: Optional[str] = None) -> Optional[dict]:
    """The reasoning level lane *session_key* carried from its local session, if any. A level was chosen for
    the carried model: once the chat runs another one (a thread ``/model``) it no longer applies."""
    route = adopt_carried_model(runner, session_key)
    if route and model and route.get('model') and route['model'] != model:
        return None
    reasoning = route.get('reasoning') if route else None
    return dict(reasoning) if isinstance(reasoning, dict) else None


def forget_carried_reasoning(runner, session_key: Optional[str]) -> None:
    """A typed ``/reasoning`` (or its reset) replaces the carried level for good."""
    store = getattr(runner, 'session_store', None)
    if not session_key or store is None:
        return
    try:
        marked = store.get_session_metadata(session_key, CARRIED)
        if isinstance(marked, dict) and marked.get('reasoning') is not None:
            store.set_session_metadata(session_key, CARRIED, {**marked, 'reasoning': None})
    except Exception:
        logger.debug('Could not drop the carried reasoning of session=%s', session_key, exc_info=True)
