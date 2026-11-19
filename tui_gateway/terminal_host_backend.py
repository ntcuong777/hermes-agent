"""Whether the active terminal backend shares the gateway host's filesystem (fork).

``tui_gateway/session_workdir.py`` keeps the upstream call sites; the decision lives here so
upstream's workdir module stays upstream's. ``local``, or a plugin backend that declares
``is_remote = False`` (nono is a sandbox around host bash): host paths and listings stay valid
there; an unregistered or remote-by-default backend does not.
"""
from __future__ import annotations

import contextlib


def terminal_backend_on_host() -> bool:
    from tui_gateway.session_workdir import _effective_terminal_backend

    backend = _effective_terminal_backend()
    if backend == "local":
        return True
    from agent.terminal_env_registry import get_provider, provider_flag

    if get_provider(backend) is None:
        # A plugin backend registers when plugins are discovered, which the gateway does on the first agent build,
        # not at launch. Until then `is_remote` read as its default (remote) and `@` completion showed nothing.
        with contextlib.suppress(Exception):
            from hermes_cli.plugins import discover_plugins

            discover_plugins()
    return provider_flag(backend, "is_remote", True) is False
