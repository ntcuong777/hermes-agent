"""The gateway's side of the out-of-turn approval lane (``tools/approval_idle.py``).

A session's approval notifier lives only for a turn, but a background subagent can ask after the turn
ended. The turn runner leaves one of these in the idle registry; it publishes the ask to the session's
attached viewers (TUI, Desktop) through the session authority, and reports unavailable while none is
attached so the asker can fall back to its own surface.

The authority-side bookkeeping lives HERE (fork-owned), not as SessionAuthority methods: it is the
approval-idle plugin's machinery (fork/session_idle_approval), and upstream's authority stays
upstream's.
"""
from __future__ import annotations

from persistence.core.errors import AuthorityRefusal


def authority_has_viewer(authority, session_id) -> bool:
    live = authority.sessions.get(session_id)
    return bool(live is not None and live.subscribers)


def authority_register_idle_approval(authority, session_id, route, data, *, timeout_s=None) -> None:
    """Show an out-of-turn ask (a background subagent's, tools/approval_idle.py) to the session's viewers.
    Stamped with the session's latest generation; it is not bound to a running claim, so it survives the
    turn boundary (see PendingControls). Raises when the session is gone or nobody is attached, which
    the ask turns into a refusal rather than a prompt nobody sees."""
    live = authority.sessions[session_id]
    with live.event_stream.lock:
        if not live.subscribers:
            raise AuthorityRefusal("no_viewer")
        generation = authority._handle(_session_ref(authority, session_id)).execution_generation
        live.controls.register(session_id, route, generation, {**data, "idle": True}, timeout_s=timeout_s)


def _session_ref(authority, session_id):
    from gateway.session_contract import SessionRef

    return SessionRef(authority.profile_id, session_id)


class IdleApprovalNotifier:
    def __init__(self, authority, session_id: str, route: str, timeout_s: float | None = None) -> None:
        self._authority, self._session_id, self._route, self._timeout_s = authority, session_id, route, timeout_s

    def available(self) -> bool:
        return authority_has_viewer(self._authority, self._session_id)

    def __call__(self, data: dict) -> None:
        authority_register_idle_approval(self._authority, self._session_id, self._route, data,
                                         timeout_s=self._timeout_s)
