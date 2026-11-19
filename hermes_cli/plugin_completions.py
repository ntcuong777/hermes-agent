"""Slash-completion words for plugin commands (fork).

``hermes_cli/commands_completion.py`` keeps only the call line; the lookup lives here so upstream's
completer stays upstream's. A plugin announces its words with
``register_command(subcommands=...)``: the first argument completes from its words, the second from
that word's choices; nothing deeper. Never raises.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

logger = logging.getLogger(__name__)


def plugin_subcommand_completions(base_cmd: str, sub_text: str, split_args: Callable,
                                  prefix_completions: Callable):
    """Completion words for a plugin command's arguments; ``split_args``/``prefix_completions``
    are the completer's own helpers (injected so this module imports nothing from upstream)."""
    def _prefix(rows: Iterable[tuple[str, Any]], partial: str):
        return prefix_completions(rows, partial)

    try:
        from hermes_cli.plugins import get_plugin_commands
        entry = get_plugin_commands().get(base_cmd[1:].lower())
        table = entry.get("subcommands") if isinstance(entry, dict) else None
        if not table:
            return
        done, partial = split_args(sub_text)
        if not done:
            rows = list(table)
        elif len(done) == 1:
            rows = list(table.get(done[0].lower(), ()))
        else:
            return
        yield from _prefix(((s, None) for s in rows), partial)
    except Exception:
        logger.debug("plugin subcommand completion failed", exc_info=True)
        return
