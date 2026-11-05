"""The gateway's side of the out-of-turn approval lane (``tools/approval_idle.py``).

A session's approval notifier lives only for a turn, but a background subagent can ask after the turn
ended. The turn runner leaves one of these in the idle registry; it publishes the ask to the session's
attached viewers (TUI, Desktop) through the session authority, and reports unavailable while none is
attached so the asker can fall back to its own surface.
"""
from __future__ import annotations


class IdleApprovalNotifier:
    def __init__(self, authority, session_id: str, route: str, timeout_s: float | None = None) -> None:
        self._authority, self._session_id, self._route, self._timeout_s = authority, session_id, route, timeout_s

    def available(self) -> bool:
        return self._authority.has_viewer(self._session_id)

    def __call__(self, data: dict) -> None:
        self._authority.register_idle_approval(self._session_id, self._route, data, timeout_s=self._timeout_s)
