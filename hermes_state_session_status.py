"""Lifecycle statuses surfaced by session pickers, classified from the final message row ONLY
so it stays O(1) per session (leaf sibling of ``hermes_state_sessions``)."""

from __future__ import annotations

from typing import Optional

SESSION_STATUS_COMPLETE = "complete"
SESSION_STATUS_INTERRUPTED = "interrupted"
SESSION_STATUS_ERROR = "error"
SESSION_STATUS_EMPTY = "empty"

# finish_reason values meaning the turn ended in a provider/agent error.
_ERROR_FINISH_REASONS = frozenset({"error", "agent_error", "content_filter"})


def classify_session_status(role: Optional[str], has_tool_calls: bool, finish_reason: Optional[str]) -> str:
    """Error finish → ``error``; assistant with pending tool_calls or a trailing user/tool row →
    ``interrupted``; otherwise ``complete`` (benign default: pickers must not alarm on unknown shapes)."""
    if (finish_reason or "").strip().lower() in _ERROR_FINISH_REASONS:
        return SESSION_STATUS_ERROR
    r = (role or "").strip().lower()
    if r in {"user", "tool"} or (r == "assistant" and has_tool_calls):
        return SESSION_STATUS_INTERRUPTED
    return SESSION_STATUS_COMPLETE
