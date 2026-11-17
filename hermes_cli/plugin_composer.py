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

_composer_text: contextvars.ContextVar[str | None] = contextvars.ContextVar("plugin_composer_text", default=None)


@contextlib.contextmanager
def bind_composer_text(text: object) -> Iterator[None]:
    token = _composer_text.set(text if isinstance(text, str) else None)
    try:
        yield
    finally:
        _composer_text.reset(token)


def composer_context() -> dict[str, str]:
    """``{"composer_text": ...}`` while bound, else nothing."""
    text = _composer_text.get()
    return {} if text is None else {"composer_text": text}
