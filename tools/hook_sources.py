"""Which plugin a hook verdict came from (fork-owned, used by the policy hooks of spec 04).

``register_hook`` has no capability gate: any enabled plugin may register ``resolve_write_policy`` or
``resolve_tool_result_limits``. The two hooks widen a restriction ("allow" under /etc, a raw spill
file), so only one plugin, ``config-seams`` (the person's own config keys), may widen; every plugin
may narrow. ``attributed`` runs the callbacks one by one and tags each result with whether its
callback was loaded as that plugin (its module is ``hermes_plugins.config_seams[.sub]``, the
namespace the loader gives a directory plugin).

This is attribution, not isolation: a plugin is in-process code and could name its module like
another. It stops a plugin that merely registers the hook with a permissive answer.
"""

from __future__ import annotations

import re
from typing import Any, Callable

TRUSTED_PLUGIN = "config-seams"
_TRUSTED_MODULE = re.compile(
    r"^hermes_plugins\." + re.escape(TRUSTED_PLUGIN.replace("-", "_")) + r"(__home_[0-9a-f]{12})?(\.|$)")


def is_trusted(callback: Callable[..., Any]) -> bool:
    """True when ``callback`` was loaded as the ``config-seams`` plugin."""
    return bool(_TRUSTED_MODULE.match(str(getattr(callback, "__module__", "") or "")))


def attributed(hook: str, **payload: Any) -> list[tuple[bool, Any]]:
    """``invoke_hook``'s results for ``hook`` as ``(from the trusted plugin, value)`` pairs.

    Same per-callback handling as ``invoke_hook`` (bounded worker and fail-closed substitution for
    the hooks that have it), one callback per dispatcher call so each result keeps its source.
    """
    from hermes_cli import plugins

    manager = plugins._delivery_manager()
    return [(is_trusted(callback), result)
            for callback in manager.iter_hook_callbacks(hook)
            for result in manager._run_hook_callbacks(hook, (callback,), dict(payload))]
