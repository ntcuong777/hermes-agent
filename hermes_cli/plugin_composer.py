"""The TUI composer text a key chord hands to a plugin slash command.

The Ink TUI sends ``composer_text`` with ``slash.exec`` when a ``display.tui_keybinds`` chord (or a
plugin's default chord, ``register_command(keybinds=)``) runs a command. ``bind_composer_text`` holds it
for the length of that call and ``invoke_plugin_command`` offers it to a handler that names a
``composer_text`` keyword. A handler that gets none was not run from a chord, or the composer holds
something text cannot carry (an attached image)."""

from __future__ import annotations

import contextlib
import contextvars
import logging
import re
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_CHORD = re.compile(r"[a-z0-9]+(\+[a-z0-9]+)*")  # ``alt+s``: modifiers and a key; the TUI decides which it honours

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


def clean_keybinds(raw: Any, owner: str = "") -> dict[str, str]:
    """``register_command(keybinds=)`` as ``{chord: args}``: lowercase chord strings and text arguments. Anything
    else (a non-dict, a chord that is not ``mod+key`` text, a non-text argument) is ignored with a warning, never
    raised: a plugin's bad default chord must not fail its registration."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        logger.warning("Plugin command %s: keybinds must be a {chord: args} dict (got %s); ignored.",
                       owner or "?", type(raw).__name__)
        return {}
    out: dict[str, str] = {}
    for chord, args in raw.items():
        spec = chord.strip().lower() if isinstance(chord, str) else ""
        if not (_CHORD.fullmatch(spec) and isinstance(args, str)):
            logger.warning("Plugin command %s: keybind %r -> %r ignored (a chord like 'alt+s' and text arguments).",
                           owner or "?", chord, args)
            continue
        out[spec] = args.strip()
    return out


def is_composer_directive(output) -> bool:
    """A handler's dict that is a well-formed ``command.dispatch`` directive (``CommandDispatchResult``);
    anything else is printed as text. Which commands a ``choices`` row's ``select``/``drop`` may name is
    the client's to run through the normal command path, which authorizes each as it would a typed one."""
    if not (isinstance(output, dict) and isinstance(output.get('type'), str)):
        return False
    from tui_gateway.contracts.tools_commands import CommandDispatchResult
    try:
        CommandDispatchResult.model_validate(output)
    except ValueError:  # pydantic's ValidationError is one
        return False
    return True

