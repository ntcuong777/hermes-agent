"""The built-in ``local`` backend routed through a plugin wall (``terminal.local_wall``).

``terminal.backend: local`` with ``terminal.local_wall: <name>`` builds the named
**plugin** backend's environment instead of the in-tree :class:`LocalEnvironment`:
the person wants the plain local backend to run under a wall (the ``nono-sandbox``
plugin) without renaming the backend in every profile. The built-in backend name
stays ``local`` everywhere — classification (``is_remote``/``is_container``/host path
resolution), dangerous-command approvals and ``env.env_type`` all keep treating the
session as local; only the environment object the terminal tool talks to comes from
the plugin.

Fail-closed when the key names a backend: a name with no registered provider, or a
provider whose environment cannot be built, raises — ``backend: local`` never
silently drops the wall the person asked for. The way out is unsetting the key (or
``/sandbox off``, which the nono plugin honours inside its own ``create_environment``).
An unset or empty key returns ``None`` and the caller builds the plain local
environment: upstream behaviour, and the patch is inert for everyone who never sets
the key.

P7 (local-wall overlay). Lives beside ``_build_local_env`` in
``tools.terminal_tool_backends.py``, which calls :func:`walled_local_environment`
before the in-tree builder.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("tools.terminal_tool")


def local_wall_backend() -> Optional[str]:
    """The plugin backend name that walls ``local``, from ``terminal.local_wall``.

    ``None`` when the key is unset or empty (plain local). The config read is
    read-only and tolerate-broken: a config problem reads as unset rather than
    breaking environment creation.
    """
    try:
        from hermes_cli.config import load_config_readonly

        section = load_config_readonly().get("terminal")
    except Exception:
        logger.debug("terminal.local_wall read failed; building plain local", exc_info=True)
        return None
    if not isinstance(section, dict):
        return None
    name = str(section.get("local_wall") or "").strip().lower()
    return name or None


def walled_local_environment(
    *, image: Optional[str], cwd: str, timeout: int, cc: Optional[dict], task_id: str,
) -> Optional[Any]:
    """The wall plugin's environment for a ``local`` backend session, or ``None``.

    ``None`` only when no wall is configured; anything configured but not satisfiable
    raises (fail closed). The provider receives the same creation arguments a plugin
    backend would, so the wall's own mode handling (``/sandbox off`` builds an
    unwrapped environment) and availability checks stay in the plugin.
    """
    name = local_wall_backend()
    if name is None:
        return None
    from agent.terminal_env_registry import get_provider

    provider = get_provider(name)
    if provider is None:
        raise RuntimeError(
            f"terminal.local_wall: {name!r} is not a registered plugin backend, so the local "
            "terminal cannot run under the wall it names. Enable the plugin (or unset "
            "terminal.local_wall to run the plain local backend).")
    logger.info("terminal.local_wall: routing the local backend through the %s plugin backend", name)
    return provider.create_environment(
        cwd=cwd, timeout=timeout, task_id=task_id, image=image, container_config=cc)
