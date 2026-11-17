"""The TUI composer text a key chord hands to a plugin slash command.

The Ink TUI sends ``composer_text`` with ``slash.exec`` when a ``display.tui_keybinds`` chord (or a
plugin's default chord, ``register_command(keybinds=)``) runs a command. ``bind_composer_text`` holds it
for the length of that call and ``invoke_plugin_command`` offers it to a handler that names a
``composer_text`` keyword. A handler that gets none was not run from a chord, or the composer holds
something text cannot carry (an attached image)."""

from __future__ import annotations

import contextlib
import contextvars
from typing import Iterator

# (text, registered command name it belongs to or None for any handler)
_composer_text: contextvars.ContextVar[tuple[str, str | None] | None] = contextvars.ContextVar(
    "plugin_composer_text", default=None)


@contextlib.contextmanager
def bind_composer_text(text: object, command: str | None = None) -> Iterator[None]:
    """Hold *text* for the call. With *command* (the registered name the chord ran), only that command's handler is
    offered it: a command a handler invokes from inside, or any other slash call while bound, gets none."""
    token = _composer_text.set((text, command) if isinstance(text, str) else None)
    try:
        yield
    finally:
        _composer_text.reset(token)


def composer_context(handler: object = None) -> dict[str, str]:
    """``{"composer_text": ...}`` while bound (and *handler* is the bound command's), else nothing."""
    bound = _composer_text.get()
    if bound is None:
        return {}
    text, command = bound
    if command is not None:
        from hermes_cli.plugins import get_plugin_command_handler
        if handler is None or get_plugin_command_handler(command) is not handler:
            return {}
    return {"composer_text": text}
