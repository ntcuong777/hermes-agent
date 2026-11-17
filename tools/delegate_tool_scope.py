"""What a delegation route may fix about a child beyond where it runs, and the two gates around it.

A :class:`agent.delegation_provider.DelegationProvider` answers "which route does this task take". Besides the route
fields the ``delegation`` section has (model, provider, endpoint, effort) a route may carry:

* ``toolsets`` - the child's toolsets, intersected with the parent's by ``_resolve_child_toolsets`` (never added to);
* ``instructions`` and ``child_profile`` - text for the child's system prompt, and the name the child and the
  ``subagent_start`` hook carry, so a plugin can grade a child against the profile it ran as;
* ``confirm`` - a launch the parent's human must approve first.

A provider may also refuse a whole call up front (``check_tasks``). This module is the whole core side of those
three: validation, the prompt section, the preflight and the confirmation. ``tools/delegate_tool_routes.py`` calls it.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Optional, Sequence

from agent.delegation_provider import DelegationProvider, DelegationRouteError

logger = logging.getLogger("tools.delegate_tool")  # log-record parity with the origin module

#: Route keys that describe the child rather than its credentials: they never take part in credential resolution,
#: so two routes differing only here resolve their credentials once.
SCOPE_KEYS = frozenset({"label", "toolsets", "instructions", "child_profile", "confirm"})

_DEFAULT_APPROVE = "Approve this launch"
_DEFAULT_DENY = "Deny"
_DEFAULT_REFUSAL = "This launch was not approved; no children launched."


def clean_scope(route: Mapping[str, Any]) -> dict[str, Any]:
    """The validated ``toolsets``/``instructions``/``child_profile``/``confirm`` of a route, as ``TaskRoute`` fields.
    A malformed value refuses the task (``DelegationRouteError``) rather than being dropped: ignoring a ``toolsets``
    that did not parse would hand the child the parent's whole set."""
    toolsets = route.get("toolsets")
    if toolsets not in (None, ""):
        if not isinstance(toolsets, (list, tuple)) or not all(isinstance(t, str) and t.strip() for t in toolsets):
            raise DelegationRouteError(f"route toolsets must be a list of toolset names (got {toolsets!r}).")
    instructions, profile = route.get("instructions"), route.get("child_profile")
    for name, value in (("instructions", instructions), ("child_profile", profile)):
        if value is not None and not isinstance(value, str):
            raise DelegationRouteError(f"route {name} must be text (got {value!r}).")
    return {
        "toolsets": tuple(dict.fromkeys(t.strip() for t in toolsets or ())),
        "instructions": (instructions or "").strip(),
        "child_profile": (profile or "").strip() or None,
        "confirm": _clean_confirm(route.get("confirm")),
    }


def _clean_confirm(raw: Any) -> Optional[dict[str, str]]:
    if raw in (None, "", {}):
        return None
    spec = {"question": raw} if isinstance(raw, str) else raw
    if not isinstance(spec, Mapping) or not isinstance(spec.get("question"), str) or not spec["question"].strip():
        raise DelegationRouteError("route confirm needs a nonempty question.")
    return {
        "question": spec["question"],
        "approve": str(spec.get("approve") or _DEFAULT_APPROVE),
        "deny": str(spec.get("deny") or _DEFAULT_DENY),
        "refusal": str(spec.get("refusal") or _DEFAULT_REFUSAL),
    }


def with_instructions(prompt: str, name: Optional[str], instructions: str) -> str:
    """``prompt`` with the route's instructions section right after its opening line (before the workspace and
    completion sections), or unchanged when the route carries no instructions."""
    if not instructions:
        return prompt
    opener, _, rest = prompt.partition("\n")
    return f"{opener}\n\nROLE: {name or ''}\n{instructions}\n{rest}"


def parent_route(parent_agent: Any) -> dict[str, Optional[str]]:
    """``{"model", "provider"}`` the delegating agent runs on (plain data for a provider; ``None`` when unknown)."""
    def text(attr: str) -> Optional[str]:
        value = getattr(parent_agent, attr, None)
        return value if isinstance(value, str) and value.strip() else None
    return {"model": text("model"), "provider": text("provider")}


def call_refusal(provider: DelegationProvider, declared: Mapping[str, Any], task_list: Sequence[Mapping[str, Any]],
                 config: Mapping[str, Any]) -> Optional[str]:
    """The provider's whole-call refusal (``check_tasks``), or None. Runs before any task is routed."""
    check = getattr(provider, "check_tasks", None)  # a duck-typed or older proxied provider has none
    if check is None:
        return None
    payloads = [{"goal": t.get("goal"), "context": t.get("context"), "index": i,
                 **{name: t[name] for name in declared if t.get(name) is not None}}
                for i, t in enumerate(task_list)]
    try:
        check(payloads, config)
    except DelegationRouteError as exc:
        return str(exc)
    except Exception as exc:  # plugin boundary: a broken check refuses (fail closed), it never lets the call through
        logger.warning("delegation provider %s.check_tasks() raised", provider.name, exc_info=True)
        return f"delegation backend '{provider.name}' failed: {exc}"
    return None


def confirm_launches(routes: Sequence[Any], parent_agent: Any) -> Optional[str]:
    """Ask the parent's human about every route that requires it, in task order, before any child is built. The
    first answer that is not an explicit approval returns that route's refusal text (the whole call is refused);
    None when every launch is approved. No approval is cached or reused: each launch asks again."""
    for route in routes:
        spec = None if route.error else route.confirm
        if spec and not _approved(parent_agent, spec):
            return spec["refusal"]
    return None


def _approved(parent_agent: Any, spec: Mapping[str, str]) -> bool:
    from tools.clarify_tool import clarify_tool

    reply = json.loads(clarify_tool(
        [{"question": spec["question"], "choices": [spec["deny"], spec["approve"]]}],
        callback=getattr(parent_agent, "clarify_callback", None),
    ))
    responses = reply.get("responses", [])
    return (
        reply.get("outcome") == "submitted"
        and len(responses) == 1
        and responses[0].get("status") == "answered"
        and responses[0].get("user_response") == spec["approve"]
    )
