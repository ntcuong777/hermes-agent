"""Plugin commands that opted in to run while the agent works (``run_while_busy=True``, P22).

The busy dispatcher's plumbing (``gateway/run_inbound.py``, ``gateway/run_inbound_hooks.py``)
keeps only the seam calls; the lookup and the dispatch live here (fork-owned).
"""
from __future__ import annotations

import functools
import inspect
import logging

logger = logging.getLogger(__name__)


def busy_plugin_handler(command: str):
    """The handler of a plugin command that opted in to run while the agent works, else None.
    Underscores normalize to hyphens like the idle path (Telegram's autocomplete form)."""
    try:
        from hermes_cli.plugins import get_plugin_commands
        entry = get_plugin_commands().get(command.replace("_", "-"))
    except Exception:
        logger.debug("busy plugin lookup failed for %s", command, exc_info=True)
        return None
    return entry.get("handler") if isinstance(entry, dict) and entry.get("run_while_busy") else None


async def dispatch_busy_plugin(self, event, source, command: str):
    """Run an opted-in plugin command on the busy path. ``None`` when the command did not opt in;
    otherwise ``(True, reply)`` like the other busy-path branches."""
    handler = busy_plugin_handler(command)
    if handler is None:
        return None
    try:
        return True, await self._hm_run_plugin_handler(handler, event, source)
    except Exception as e:
        logger.warning("Plugin command dispatch failed while busy: %s", e, exc_info=True)
        return True, f"/{command} failed: {e}"
