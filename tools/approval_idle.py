"""Out-of-turn approval lane: a background subagent's ask when its parent's turn has ended.

The gateway registers a session's approval notifier only for the length of a turn
(``gateway/run_turn_runner.py``). A background ``delegate_task`` child outlives that turn, so its ask
found no notifier and could only be pushed to a messaging chat. This module is the opt-in way to reach
the parent's viewers (TUI, Desktop) anyway:

* the gateway registers one *idle notifier* per session key (``register_idle_notify``) and leaves it in
  place after the turn; it answers ``available()`` only while a viewer is attached;
* a caller that wants the lane wraps its ask in ``idle_ask()``. Every other approval, in or out of a
  turn, keeps today's behaviour. Inside the context, the lane is the delivery path when no turn
  notifier is registered; when one is, the ask still rides the turn notifier but is flagged ``idle``
  so the turn-end sweep (``tools.approval.unregister_gateway_notify``) leaves it waiting — a child
  that asked mid-turn outlives that turn.

An entry that rides this lane is flagged ``idle`` so the turn-end sweep
(``tools.approval.unregister_gateway_notify``) leaves it waiting for its answer.
"""

from __future__ import annotations

import contextlib
import contextvars
import threading
from typing import Any, Iterator, Optional

_lock = threading.Lock()
_notifiers: dict[str, Any] = {}
_allowed: contextvars.ContextVar[bool] = contextvars.ContextVar("approval_idle_ask_allowed", default=False)


def register_idle_notify(session_key: str, notifier: Any) -> None:
    """``notifier(approval_data)`` publishes an out-of-turn prompt; ``notifier.available()`` says whether
    anyone could see it. Replaces the session's previous notifier (one per key)."""
    with _lock:
        _notifiers[session_key] = notifier


def unregister_idle_notify(session_key: str) -> None:
    with _lock:
        _notifiers.pop(session_key, None)


def idle_notify_for(session_key: str) -> Optional[Any]:
    """The session's idle notifier, or None when none is registered or no viewer could see the prompt."""
    with _lock:
        notifier = _notifiers.get(session_key)
    return notifier if notifier is not None and notifier.available() else None


@contextlib.contextmanager
def idle_ask() -> Iterator[None]:
    """Allow approvals raised inside this block to use the idle lane (a thread's own context only)."""
    token = _allowed.set(True)
    try:
        yield
    finally:
        _allowed.reset(token)


def idle_ask_allowed() -> bool:
    return _allowed.get()
