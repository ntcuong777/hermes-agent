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


def builtin_word_completions(base_cmd: str, sub_text: str, split_args: Callable, prefix_completions: Callable,
                             builtin_words: Iterable[str] = ()):
    """Words plugins added to a BUILT-IN command with ``register_command_words`` (``/memory relearn``): the first
    argument offers the added words the built-in's own table lacks, the second the choices of the word typed
    (``/memory approve`` -> ``all``). The built-in's own words are offered by the caller, first and unchanged."""
    try:
        from hermes_cli.plugins import get_command_words
        table = get_command_words().get(base_cmd[1:].lower())
        if not table:
            return
        done, partial = split_args(sub_text)
        if not done:
            own = {str(w).lower() for w in builtin_words}
            rows = [w for w in table if w not in own]
        elif len(done) == 1:
            rows = list(table.get(done[0].lower(), ()))
        else:
            return
        yield from prefix_completions(((s, None) for s in rows), partial)
    except Exception:
        logger.debug("plugin built-in word completion failed", exc_info=True)
        return
