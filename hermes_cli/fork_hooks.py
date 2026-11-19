"""Hook names the fork adds to upstream ``VALID_HOOKS``.

``hermes_cli.plugins`` unions this set in. The names are the registry: plugins
attach by them, and the call sites stay one line in the upstream files.
"""

FORK_HOOKS = {
    # transform_request_messages: per provider request, where select_context lands.
    # Return a replacement message list or None. First valid list wins.
    "transform_request_messages",
    # transform_status_bar_fields: session.usage bar rows. Must not block.
    "transform_status_bar_fields",
    # Session-lane seam (gateway/session_lane_seam.py).
    "observe_session_check",
    "wrap_session_call",
    "route_lane_operation",
    # Spec 04 seams.
    "resolve_write_policy",
    "resolve_tool_result_limits",
    "resolve_reply_max_tokens",
}
