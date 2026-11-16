"""Sessions a local surface opens whose writer is a messaging lane: the Discord session mirror's
bound thread (``plugins/platforms/discord/adapter_session_mirror.py``).

The mirror binds a local TUI session to a thread with ``/resume <sid> --all``: the row moves onto the
thread's lane (``source='discord'``, the thread's session key, the mirror admin as ``user_id``) and
its local receipt is released, so the thread is the session's one writer and the TUI that created it
attaches as a viewer. Every check that reads "a session a local surface opens is a LOCAL route"
(``authorize_local_source``, ``policy_for_source``) answers no for it. This module is where a
handler asks the other question, "does a lane this host's mirror bound own it?", and what it may
then do:

* ``bound_lane``: the lane's adapter, only when the adapter takes local forwards
  (``accepts_local_forward``) AND it bound THIS session (``owns_local_forward``). Another user's
  Discord or Telegram chat answers None, so every refusal it had stands.
* ``lane_owned_refusal``: an operation that writes the conversation (``/compress``, ``/undo``,
  ``/branch``, ``/background``...) never runs here: the client re-sends the typed command through
  ``slash.exec``, which relays it to the thread (``forward_command_to_lane``). The reason is its own
  (``lane_owned``), never ``permission_denied``.
* ``viewer_policy``: the launch policy a local-only effect of the bound session reads (checkpoint
  reads, a staged file, a ``!cmd`` in its folder): the profile's config at the folder the session
  recorded. Nothing that runs a turn uses it.
"""
from __future__ import annotations

from contextvars import ContextVar
import json
from pathlib import Path

from gateway.config import Platform
from hermes_state_runtime import RuntimeStoreError


def lane_source(row):
    """The session's lane source from its stored origin, or None for a row no messaging lane
    serves (local/api rows keep their own owner rules: there is no lane to forward into)."""
    if not row:
        return None
    from gateway.session import SessionSource
    try:
        origin = json.loads(row.get('origin_json') or '')
        source = SessionSource.from_dict(origin) if isinstance(origin, dict) else None
    except (TypeError, ValueError, KeyError):
        return None
    if source is None or source.platform in (Platform.LOCAL, Platform.API_SERVER):
        return None
    return source


def bound_lane(authority, session_id):
    """The adapter of the lane that owns *session_id* when this host's mirror bound it to that lane
    (``owns_local_forward``), else None. Reads the row: callers off the loop or on a cold path."""
    row = authority.db.get_session(session_id) if session_id else None
    source = lane_source(row)
    if source is None:
        return None
    resolve = getattr(getattr(authority, 'runner', None), '_adapter_for_source', None)
    adapter = resolve(source) if resolve is not None else None
    if adapter is None or not getattr(adapter, 'accepts_local_forward', False):
        return None
    owns = getattr(adapter, 'owns_local_forward', None)
    # An adapter that cannot say which sessions it bound is not trusted with any of them.
    return adapter if callable(owns) and owns(session_id) else None


def lane_owned_refusal(command):
    """The refusal a transcript-writing operation gets on a lane-owned session: the client relays
    *command* (the typed form, e.g. ``/compress``) through ``slash.exec`` instead."""
    return RuntimeStoreError('lane_owned', field='session_id',
                             detail=f'its bound thread writes this conversation: {command} is sent there')


def viewer_policy(authority, ref):
    """A launch policy for the local-only effects of a lane-owned session, or None when no lane this
    host bound owns *ref*. The frozen local policy went with the receipt at the binding; the lane's own
    turns read the profile's config, so this is that config at the folder the session recorded."""
    if bound_lane(authority, ref.session_id) is None:
        return None
    row = authority.db.get_session(ref.session_id) or {}
    cwd = row.get('cwd')
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise RuntimeStoreError('cwd_unavailable', field='cwd', detail='the session has no working folder here')
    from gateway.session_local import load_profile_config
    from gateway.session_policy import build_policy
    return build_policy({'source': 'tui', 'cwd': cwd}, load_profile_config(Path(authority.profile_id)))


# ── the dispatch seam: a refusal any handler (today's or a later one) raises for a lane-owned session ──
#
# Handlers written for local sessions refuse anything else with ``permission_denied`` (no LOCAL source,
# no frozen policy). Rather than teach each of them, today's and tomorrow's, about mirror-bound
# sessions, ``AuthorityConnection`` routes such a refusal here by the class of the call, which is read
# off the call itself: the capabilities the handler asked ``SessionAuthority.authorize`` for. A handler
# that checked only ``session:read`` is a read and is answered locally (re-run with the session's
# ``viewer_policy`` standing in for the missing frozen policy); anything else, or a handler that never
# asked, is a write and becomes ``lane_owned``, which the TUI answers by relaying the typed command to
# the bound thread. No method list: a method added later inherits its class from what it authorizes.

_CHECKS = ContextVar('lane_authorize_checks', default=None)
_VIEWER_POLICY = ContextVar('lane_viewer_policy', default=None)


def note_authorize(session_id, capability):
    """``SessionAuthority.authorize`` reports each check, so the seam knows what the call needed."""
    checks = _CHECKS.get()
    if checks is not None:
        checks.append((session_id, capability))


def viewer_policy_override():
    """The policy ``policy_for_source`` answers for a non-LOCAL source while a read of a lane-owned
    session is re-run (``rescue``); None at any other time."""
    return _VIEWER_POLICY.get()


async def call_with_rescue(connection, ref, call):
    """``await call()``; a ``permission_denied`` on a session this host's mirror bound, for its owner,
    is answered by class (see above) instead of surfacing. Every other outcome passes through."""
    checks = []
    token = _CHECKS.set(checks)
    try:
        return await call()
    except RuntimeStoreError as exc:
        if exc.reason != 'permission_denied' or getattr(exc, '_lane_rescued', False):
            raise
        return await _rescue(connection, ref, call, exc, checks)
    finally:
        _CHECKS.reset(token)


async def _rescue(connection, ref, call, exc, checks):
    import asyncio
    sid = ref.session_id if isinstance(ref.session_id, str) else ''
    asked = {capability for session_id, capability in checks if session_id == sid}
    actor = connection.actor
    if (not sid or not asked <= set(actor.capabilities)  # refused at the capability gate: a real refusal
            or not (sid in connection.subscriptions or 'session:operator' in actor.capabilities)
            or await asyncio.to_thread(bound_lane, connection.authority, sid) is None):
        raise exc
    if asked == {'session:read'}:
        try:
            policy = await asyncio.to_thread(viewer_policy, connection.authority, ref)
        except RuntimeStoreError:
            policy = None
        if policy is not None:
            token = _VIEWER_POLICY.set(policy)
            try:
                return await call()
            except RuntimeStoreError as again:
                if again.reason != 'permission_denied':
                    raise
            finally:
                _VIEWER_POLICY.reset(token)
        # A read nothing local can answer: the thread can (its /status, /context, ...).
    refusal = lane_owned_refusal('the command')
    refusal._lane_rescued = True
    raise refusal from exc
