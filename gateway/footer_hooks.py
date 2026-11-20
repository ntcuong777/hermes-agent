"""Plugin contributions to the runtime footer and to ``/usage``.

Two hooks, both returning ``str | None`` (a plugin with nothing to say returns ``None``):

* ``footer``: appended under the built-in footer fields of a final reply.
* ``usage_extra``: appended to the ``/usage`` output as one block.

A plugin that raises is logged and skipped; it never costs the reply or the command.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _contributions(hook: str, **kwargs: Any) -> list[str]:
    from hermes_cli.plugins import invoke_hook

    try:
        results = invoke_hook(hook, **kwargs)
    except Exception:  # health: allow BLE001 -- a hook must not break the reply
        logger.warning("%s hook raised; its contribution is skipped", hook, exc_info=True)
        return []
    return [text.strip("\n") for text in results if isinstance(text, str) and text.strip()]


def with_footer_extras(line: str, **context: Any) -> str:
    """``line`` followed by every ``footer`` hook's block, one per line; ``line`` itself when none contribute."""
    extras = _contributions("footer", **context)
    return "\n".join([line, *extras]) if line and extras else (line or "\n".join(extras))


def usage_extra_lines(**context: Any) -> list[str]:
    """The ``usage_extra`` hooks' text, split into the lines ``/usage`` appends."""
    return [row for block in _contributions("usage_extra", **context) for row in block.split("\n")]
