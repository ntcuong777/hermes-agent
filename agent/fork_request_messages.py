"""The transform_request_messages egress hook.

Lives outside ``conversation_loop`` so that file keeps a call, not the chain.
Fail-open: an exception or a bad return leaves the request list unchanged.
"""

from __future__ import annotations

from typing import Any


def is_request_message_list(value: Any) -> bool:
    """A non-empty list of dicts may replace the outgoing request."""
    return isinstance(value, list) and bool(value) and all(isinstance(m, dict) for m in value)


def apply_transform_request_messages(agent: Any, api_messages: list, *, logger: Any) -> list:
    """Run the hook. Returns ``api_messages`` when nothing accepted replaces it."""
    try:
        from hermes_cli.lifecycle import has_hook
        if not has_hook("transform_request_messages"):
            return api_messages
    except Exception:
        logger.debug("transform_request_messages has_hook probe failed; skipping", exc_info=True)
        return api_messages
    from agent.conversation_loop import _clone_message_for_send
    from agent.message_metadata import without_persistence_fields
    from hermes_cli.plugins import invoke_hook_chained

    session_label = getattr(agent, "session_id", None) or "-"
    base = [_clone_message_for_send(m) for m in api_messages]
    try:
        result = invoke_hook_chained(
            "transform_request_messages", "messages", accept=is_request_message_list,
            messages=base, session_id=session_label,
            model=getattr(agent, "model", None) or "",
            platform=getattr(agent, "platform", None) or "",
        )
    except Exception:
        logger.warning(
            "transform_request_messages plugin hook failed; using unmodified request messages (session=%s)",
            session_label, exc_info=True)
        return api_messages
    if result is base or not is_request_message_list(result):
        return api_messages
    stripped = [without_persistence_fields(m) for m in result]
    return result if all(a is b for a, b in zip(stripped, result)) else stripped
