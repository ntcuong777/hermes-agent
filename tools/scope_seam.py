"""The delegation child-scope seam (fork): one module, one import per surface.

Core upstream knows nothing of a child's route scope. subagent-scope replaces every stub here
(``tools.scope_seam:<fn>``); with the plugin absent each stub FAILS CLOSED: a route that carries
scope keys (``toolsets``/``instructions``/``child_profile``/``confirm``) refuses the build instead of
running the child with what the provider asked for, and a launch that needs a human confirmation is
refused, never launched unapproved. The route vocabulary itself (the ``TaskRoute`` fields and the
``ROUTE_KEYS``) stays in ``tools/delegate_tool_routes.py`` and ``agent/delegation_provider.py`` —
it is the provider contract, not behaviour.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

import json
from dataclasses import replace


def _scope_child_prompt(prompt: str, child_profile: Optional[str], instructions: str) -> str:
    """Seam. The plugin inserts the route's instructions. Absent, instructions refuse the build."""
    del child_profile
    if instructions:
        raise ValueError("child instructions were not applied")
    return prompt


def _stamp_child_profile(child: Any, child_profile: Optional[str]) -> None:
    """Seam. The plugin sets ``child._delegate_profile``. Absent, a profile refuses the build."""
    if child_profile:
        raise ValueError("child profile was not applied")


def _child_scope_build(route: Any, task: dict, top_role: str):
    """Seam. Returns ``(role, kwargs)``. Absent, a route that carries scope refuses the build."""
    if (getattr(route, "toolsets", ()) or getattr(route, "instructions", "")
            or getattr(route, "child_profile", None)):
        raise ValueError("child scope was not applied")
    return (task.get("role") or top_role), {"toolsets": None, "instructions": "", "child_profile": None}


def _confirm_ready(parent_agent: Any, runnable: list, spent_before: int):
    """Seam. The plugin asks the person. Absent, a route that requires approval is refused."""
    del parent_agent, spent_before
    for route in runnable:
        if getattr(route, "confirm", None):
            return getattr(route, "confirm").get("refusal") or "This launch was not approved; no children launched."
    return None


# The route resolution itself stays in ``tools/delegate_tool_routes.py`` (its ``TaskRoute``
# child-scope fields and the fail-closed refusal in ``_route_to_task_route``). This stub keeps the
# registry's one target here and callers' one import; it forwards verbatim.
def resolve_task_routes(task_list: list[dict[str, Any]], config: Mapping[str, Any], parent_agent: Any,
                        confirm: bool = False) -> tuple[list[TaskRoute], Optional[str]]:
    """Seam. subagent-scope replaces this. Absent, upstream resolution runs; a route that carries a
    child scope is refused in ``_route_to_task_route`` (fail closed)."""
    from tools.delegate_tool_routes import resolve_task_routes as _upstream

    return _upstream(task_list, config, parent_agent, confirm=confirm)
