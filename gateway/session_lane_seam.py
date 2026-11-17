"""Session-lane seam: where a plugin owns what happens to a session whose lane is a platform thread.

Fork-owned (never upstream). A *lane* is the messaging chat (a Discord thread, say) that writes a Hermes
session while a local surface (TUI, desktop) is attached to it as a viewer. Upstream knows only two kinds
of session owner: a local surface, and a messaging chat that no local surface touches. This module is the
one place the gateway's session authority asks "does a lane own this?" and hands the answer to plugins:
three hooks, each dispatched here, each with one-line call sites in the upstream-owned files.

Hooks (names are in ``hermes_cli.plugins.VALID_HOOKS``; every callback runs INLINE, unbounded, so it must
not block; each callback may be sync or ``async def``):

* ``observe_session_check(session_id, capability)``: ``SessionAuthority.authorize`` reports each capability
  check, so a plugin can tell what a call needed. No return value.
* ``wrap_session_call(connection, ref, method)``: once per RPC the connection dispatches. Return ``None``
  (no opinion) or a *wrapper*: ``wrapper(call) -> call2``, both zero-argument async callables. The wrapper
  may run ``call`` zero, one or several times, translate its refusal, or re-run it in another scope. Wrappers
  stack in registration order (the last registered is outermost).
* ``route_lane_operation(kind, **ctx)``: a local surface touched a session a lane may own. ``kind`` names the
  operation (the ``KINDS`` below) and ``ctx`` its inputs. A callback returns ``None`` (no opinion), a value of
  the kind's result type, or a ``RuntimeStoreError`` INSTANCE to refuse (never raise: the plugin manager
  isolates a raising callback, which would turn a deliberate refusal into silence).

FAIL-OPEN, everywhere: with no plugin, a missing hook, a callback that raises, a malformed result or a bug
in this module, every dispatcher returns what upstream's code did before the seam existed (its ``default``).
Nothing here may stop or break a call; a refusal reaches the caller only when a plugin returned a
``RuntimeStoreError`` on purpose.

Which lane features a core has taken out of the upstream-owned files is recorded in
``gateway/session_lane_features.py`` (``REMOVED_FROM_CORE``), not here.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("gateway.session_lane_seam")

HOOK_OBSERVE = "observe_session_check"
HOOK_WRAP = "wrap_session_call"
HOOK_ROUTE = "route_lane_operation"
HOOKS = (HOOK_OBSERVE, HOOK_WRAP, HOOK_ROUTE)

#: ``route_lane_operation`` kinds and what each call site expects back (None = no opinion for all of them).
KINDS = {
    "submit": "dict: a receipt-shaped result for a prompt.submit the lane took (ctx: connection, ref, params, error)",
    "command_refused": "dict: the result of a command the lane ran in place of upstream's refusal "
                       "(ctx: connection, ref, name, arg, verdict, live)",
    "nonlocal_command": "dict: the result; RUN_HERE: run it here; RuntimeStoreError: refuse "
                        "(ctx: connection, ref, name, arg, runner_command, capability, verdict)",
    "attach_info": "dict: extra fields for the attach reply (ctx: authority, ref)",
    "complete_info": "None (the callback edits ctx['info'] in place; ctx: authority, live, info, session_id, policy)",
    "handoff_route": "None (a sync callback inside the lane-move DB write; ctx: conn, target, policy)",
    "rollback_policy": "a launch policy for a checkpoint read; RuntimeStoreError: refuse "
                       "(ctx: authority, ref, op: 'list', 'diff', 'restore', ...)",
}

#: What a ``nonlocal_command`` callback returns to say "run this command here, as for a local session".
RUN_HERE = "run-here"

_WARNED: set[str] = set()


def _warn_once(key: str, message: str, *args: Any) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        logger.warning(message, *args, exc_info=True)


def _wanted(hook: str) -> bool:
    """True when a loaded plugin handles *hook*. Never raises: no plugin system means no."""
    try:
        from hermes_cli.plugins import has_hook

        return bool(has_hook(hook))
    except Exception:  # health: allow BLE001 -- fail open: no plugin system, upstream's behaviour
        return False


def observe_check(session_id: Any, capability: Any) -> None:
    """One call per ``SessionAuthority.authorize``: tell the plugins what the call needs."""
    if not _wanted(HOOK_OBSERVE):
        return
    try:
        from hermes_cli.plugins import invoke_hook

        invoke_hook(HOOK_OBSERVE, session_id=session_id, capability=capability)
    except Exception:  # health: allow BLE001 -- fail open
        _warn_once("observe", "session lane: %s dispatch failed (ignored)", HOOK_OBSERVE)


class _Once:
    """A zero-argument async callable over ``call`` that remembers its FIRST outcome."""

    def __init__(self, call: Callable[[], Awaitable[Any]]) -> None:
        self._call = call
        self.started = False
        self._outcome: Optional[tuple] = None

    async def __call__(self) -> Any:
        first = not self.started
        self.started = True
        try:
            result = await self._call()
        except Exception as exc:  # health: allow BLE001 -- recorded, then re-raised unchanged
            if first:
                self._outcome = ("raise", exc)
            raise
        if first:
            self._outcome = ("return", result)
        return result

    def replay(self) -> Any:
        kind, value = self._outcome  # type: ignore[misc]
        if kind == "raise":
            raise value
        return value


def _is_refusal(exc: BaseException) -> bool:
    """A deliberate refusal: the state authority's own error type. Anything else escaping a wrapper is a bug."""
    return type(exc).__name__ == "RuntimeStoreError"


async def guard(connection: Any, ref: Any, method: Any, call: Callable[[], Awaitable[Any]]) -> Any:
    """``await call()``, through the wrappers plugins returned for this RPC (see ``wrap_session_call``).

    A wrapper that raises ``RuntimeStoreError`` refuses on purpose and is honoured. Any other exception out
    of the wrapper chain is a plugin bug: if ``call`` had not started it runs plain; if it had, the caller gets
    the outcome of its first run, exactly what it would have seen without the plugin."""
    if not _wanted(HOOK_WRAP):
        return await call()
    once = _Once(call)
    try:
        from hermes_cli.plugins import invoke_hook

        wrappers = [w for w in invoke_hook(HOOK_WRAP, connection=connection, ref=ref, method=method)
                    if callable(w)]
        chained: Callable[[], Awaitable[Any]] = once
        for wrapper in wrappers:
            try:
                candidate = wrapper(chained)
            except Exception:  # health: allow BLE001 -- a wrapper that cannot be built is skipped
                _warn_once("wrap-build", "session lane: a %s wrapper could not be built (skipped)", HOOK_WRAP)
                continue
            if callable(candidate):
                chained = candidate
    except Exception:  # health: allow BLE001 -- fail open
        _warn_once("wrap", "session lane: %s dispatch failed (ignored)", HOOK_WRAP)
        return await call()
    try:
        return await chained()
    except Exception as exc:  # health: allow BLE001 -- split below: deliberate refusal, call's own error, plugin bug
        if _is_refusal(exc):
            raise
        if once.started:
            if once._outcome is not None:
                return once.replay()  # the call's first outcome, whatever the wrapper did after it
            raise
        _warn_once("wrap-run", "session lane: a %s wrapper failed before the call ran (call run plain)", HOOK_WRAP)
        return await call()


async def route(kind: str, **ctx: Any) -> Any:
    """The first non-None answer of the ``route_lane_operation`` callbacks for *kind*, or None.

    A ``RuntimeStoreError`` instance in the results is raised (a deliberate refusal); results of another
    exception type are ignored. Callback exceptions never get here: the plugin manager reports and skips them."""
    if not _wanted(HOOK_ROUTE):
        return None
    try:
        from hermes_cli.plugins import ainvoke_hook

        results = await ainvoke_hook(HOOK_ROUTE, kind=kind, **ctx)
    except Exception:  # health: allow BLE001 -- fail open
        _warn_once("route", "session lane: %s dispatch failed (ignored)", HOOK_ROUTE)
        return None
    return _first(results)


def route_sync(kind: str, **ctx: Any) -> Any:
    """:func:`route` for call sites that are not coroutines (callbacks must be sync functions here)."""
    if not _wanted(HOOK_ROUTE):
        return None
    try:
        from hermes_cli.plugins import invoke_hook

        results = invoke_hook(HOOK_ROUTE, kind=kind, **ctx)
    except Exception:  # health: allow BLE001 -- fail open
        _warn_once("route-sync", "session lane: %s dispatch failed (ignored)", HOOK_ROUTE)
        return None
    return _first(results)


def _first(results: Any) -> Any:
    for item in results or ():
        if isinstance(item, BaseException):
            if _is_refusal(item):
                raise item
            continue
        if inspect.iscoroutine(item):  # a sync dispatch met an async callback's coroutine: not usable here
            item.close()
            continue
        return item
    return None


# ── per-kind dispatchers: the one-line call sites in upstream files call these ─────────────────────────────


async def submit_refused(connection: Any, ref: Any, params: Any, error: BaseException) -> Optional[dict]:
    """A prompt.submit the canonical store refused ``permission_denied``: a lane may have taken it (receipt dict)."""
    found = await route("submit", connection=connection, ref=ref, params=params, error=error)
    return found if isinstance(found, dict) else None


async def command_refused(connection: Any, ref: Any, name: str, arg: str, verdict: str, live: Any) -> Optional[dict]:
    """A command upstream would refuse (``unsupported_command``) on a session a lane may own: the lane's result."""
    found = await route("command_refused", connection=connection, ref=ref, name=name, arg=arg,
                        verdict=verdict, live=live)
    return found if isinstance(found, dict) else None


async def nonlocal_command(connection: Any, ref: Any, name: str, arg: str, runner_command: Any,
                           capability: str, verdict: str) -> Optional[dict]:
    """A command a local surface sent to a session that is not one of its local routes.

    Upstream refuses it (``permission_denied``), and so does this unless a plugin answers: a dict (the lane ran
    the command), ``RUN_HERE`` (run it here as for a local session; this returns None) or its own refusal."""
    found = await route("nonlocal_command", connection=connection, ref=ref, name=name, arg=arg,
                        runner_command=runner_command, capability=capability, verdict=verdict)
    if isinstance(found, dict):
        return found
    if found == RUN_HERE:
        return None
    from hermes_state_runtime import RuntimeStoreError

    raise RuntimeStoreError("permission_denied")  # no answer, or a malformed one: upstream's refusal


async def attach_info(authority: Any, ref: Any) -> dict:
    """Extra reply fields for a viewer that attaches to a session with no frozen local policy (model/effort pills)."""
    found = await route("attach_info", authority=authority, ref=ref)
    return found if isinstance(found, dict) else {}


def complete_info(authority: Any, live: Any, info: dict, session_id: str, policy: Any) -> None:
    """Let a plugin fill the model/effort of a session-info dict (edited in place)."""
    route_sync("complete_info", authority=authority, live=live, info=info, session_id=session_id, policy=policy)


def record_handoff(conn: Any, target: str, policy: Any) -> None:
    """Inside the write that moves a local conversation onto a messaging lane (``hand_off_conversations``):
    a plugin may record the released local policy's route for *target* (same transaction, same *conn*)."""
    route_sync("handoff_route", conn=conn, target=target, policy=policy)


async def rollback_policy(authority: Any, ref: Any, op: str) -> Any:
    """The launch policy a checkpoint operation runs under on a policy-less session. Upstream refuses with
    ``permission_denied``: so does this unless a plugin supplies a policy (or its own refusal)."""
    from hermes_state_runtime import RuntimeStoreError

    found = await route("rollback_policy", authority=authority, ref=ref, op=op)
    if found is None:
        raise RuntimeStoreError("permission_denied")
    return found
