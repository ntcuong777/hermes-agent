"""Per-launch model overrides preserve role scope and require fresh human consent."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from tools.delegate_roles import RoleProfile

_APPROVE = "Approve this launch"


def confirmed_model_profile(
    task: dict[str, Any],
    profile: RoleProfile | None,
    parent_agent: Any,
    defaults: dict[str, Any],
) -> RoleProfile | None:
    if "model" not in task and "provider" not in task:
        return profile
    if profile is None:
        raise ValueError("A model override requires a declared role.")
    default_model = profile.model or defaults["model"]
    default_provider = profile.provider or defaults["provider"]
    model = _route_field(task, "model", default_model)
    provider = _route_field(task, "provider", default_provider)
    if (model, provider) == (default_model, default_provider):
        return profile
    rationale = task.get("model_rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("A nondefault model requires nonempty model_rationale.")
    question = (
        f"Change model for this one subagent launch? Role: {profile.name}. "
        f"Task: {task['goal']}\nDefault: {default_provider}/{default_model}\n"
        f"Requested: {provider}/{model}\nRationale: {rationale}\n"
        "Role tools and scope stay unchanged. This approval is not reused."
    )
    if not _confirmed(parent_agent, question):
        raise ValueError(
            f"Model override for role {profile.name!r} was not approved; no children launched."
        )
    route_provider = profile.provider if provider == default_provider else provider
    return replace(profile, model=model, provider=route_provider)


def _route_field(task: dict[str, Any], name: str, default: str | None) -> str | None:
    value = task.get(name, default)
    if name in task and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"Task {name} must be a nonempty string.")
    return value


def _confirmed(parent_agent: Any, question: str) -> bool:
    from tools.clarify_tool import clarify_tool

    reply = json.loads(
        clarify_tool(
            [{"question": question, "choices": ["Deny override", _APPROVE]}],
            callback=getattr(parent_agent, "clarify_callback", None),
        )
    )
    responses = reply.get("responses", [])
    return (
        reply.get("outcome") == "submitted"
        and len(responses) == 1
        and responses[0].get("status") == "answered"
        and responses[0].get("user_response") == _APPROVE
    )
