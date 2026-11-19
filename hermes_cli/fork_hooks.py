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


def invoke_hook_chained(manager, hook_name: str, chain_key: str, *, accept=None, **kwargs):
    """Rewrite hook: each callback's accepted return feeds the next. Fail-open."""
    callbacks = manager._hooks.get(hook_name)
    if not callbacks:
        return None
    from hermes_cli.plugins import _resolve_hook_callback_timeout
    from hermes_cli.plugins_dispatch import (
        _HOOK_CHAINED_BOUNDED_HOOKS, _HOOK_SKIPPED, _hook_uses_callback_timeout,
    )
    timeout = _resolve_hook_callback_timeout()
    bounded = hook_name in _HOOK_CHAINED_BOUNDED_HOOKS and _hook_uses_callback_timeout(hook_name, timeout)
    current = kwargs.get(chain_key)
    for cb in callbacks:
        payload = dict(kwargs)
        payload[chain_key] = current
        try:
            if bounded:
                ret = manager._run_hook_callback_bounded(hook_name, cb, payload, timeout)
                if ret is _HOOK_SKIPPED:
                    continue
            else:
                ret = manager._invoke_hook_callback(cb, payload)
        except (Exception, SystemExit) as exc:
            import logging
            logging.getLogger("hermes_cli.plugins").debug(
                "hook '%s' callback chain failure", hook_name, exc_info=exc)
            manager._report_hook_failure(hook_name, cb, payload, exc)
            continue
        if ret is None:
            continue
        if accept is None or accept(ret):
            current = ret
    return current
