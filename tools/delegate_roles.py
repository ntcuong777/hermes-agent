"""Role profiles for ``delegate_task``: a task's ``role`` picks its child's toolsets, model and instructions.

Hermes' own tool ignores ``role`` (capability is depth-derived) and hands every child the parent's toolsets and one
shared model. A plugin can register a *provider* here: given a role name it returns a :class:`RoleProfile` (or None),
and ``delegate_task`` then builds that task's child from the profile. The core never learns what else a plugin
attaches to a role (filesystem scope, web policy, budgets): it only carries the profile's name on the child and in the
``subagent_start`` event, so the plugin can grade what the child does.

With no provider registered nothing here applies and ``delegate_task`` behaves exactly as before. The depth roles
``leaf`` and ``orchestrator`` are never profiles.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

logger = logging.getLogger(__name__)

#: The old depth-derived roles a task may still name; never resolved as profiles.
LEGACY_ROLES = ("leaf", "orchestrator")


@dataclass(frozen=True)
class RoleProfile:
    """What a role fixes about a child. An empty ``toolsets`` inherits the parent's; a toolset the parent lacks is
    dropped, never added (``_resolve_child_toolsets`` intersects)."""

    name: str
    toolsets: tuple[str, ...] = ()
    model: Optional[str] = None
    #: Provider that serves ``model``. Without it the child keeps the parent's provider, so a model of another
    #: provider would be sent to the parent's endpoint (404). It wins over ``delegation.provider``, as ``model`` does.
    provider: Optional[str] = None
    instructions: str = ""
    reasoning_effort: Optional[str] = None
    #: One line shown to the model next to the role's name in the tool schema.
    description: str = ""


Resolve = Callable[[str], Optional[RoleProfile]]
Catalog = Callable[[], Mapping[str, str]]

_PROVIDERS: dict[str, tuple[Resolve, Catalog]] = {}
_LOCK = threading.Lock()


def register_role_provider(name: str, resolve: Resolve, catalog: Catalog) -> None:
    """Register (or replace, by ``name``: a plugin reload must not stack) a source of role profiles."""
    with _LOCK:
        _PROVIDERS[name] = (resolve, catalog)


def unregister_role_provider(name: str) -> None:
    with _LOCK:
        _PROVIDERS.pop(name, None)


def role_catalog() -> dict[str, str]:
    """``{role name: one-line description}`` across every provider; a provider that fails contributes nothing."""
    with _LOCK:
        providers = list(_PROVIDERS.items())
    merged: dict[str, str] = {}
    for provider_name, (_resolve, catalog) in providers:
        try:
            merged.update({str(k): str(v or "") for k, v in dict(catalog()).items()})
        except Exception:
            logger.warning(
                "delegate role provider %r: catalog failed",
                provider_name,
                exc_info=True,
            )
    return merged


def profile_for(raw: Any) -> Optional[RoleProfile]:
    """The profile a task's ``role`` names, or None when the task names no profile (no role, a depth role, or no
    provider registered). Raises ``ValueError`` when providers exist and none knows the name, or one fails: a task
    that asked for a role must never be built as if it had asked for none."""
    name = str(raw).strip().lower() if raw else ""
    if not name or name in LEGACY_ROLES:
        return None
    with _LOCK:
        providers = list(_PROVIDERS.items())
    if not providers:
        return None
    for provider_name, (resolve, _catalog) in providers:
        try:
            profile = resolve(name)
        except Exception:
            logger.warning(
                "delegate role provider %r failed on %r",
                provider_name,
                name,
                exc_info=True,
            )
            continue
        if profile is not None:
            return profile
    declared = ", ".join(sorted(role_catalog())) or "(none)"
    raise ValueError(
        f"Unknown role {raw!r}. Declared roles: {declared}. Give each task one of them in `role`."
    )


def reasoning_kwargs(profile: Optional[RoleProfile]) -> dict[str, Any]:
    """``_build_child_agent`` kwargs carrying the profile's reasoning effort; empty when it names none or an unusable one."""
    if profile is None or not profile.reasoning_effort:
        return {}
    from hermes_constants import parse_reasoning_effort

    config = parse_reasoning_effort(profile.reasoning_effort)
    return {} if config is None else {"reasoning_config": config}
