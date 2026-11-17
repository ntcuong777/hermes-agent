"""Pluggable whole-turn agent runtimes.

A runtime owns an entire turn (the model loop, not only the wire dialect): the codex
app-server subprocess is the in-tree one. A plugin claims another by name::

    from agent.agent_runtimes import register_agent_runtime
    register_agent_runtime("my_runtime", run_turn, claims=lambda route: route.provider == "mine")

The name doubles as the ``api_mode`` the agent carries, so a runtime is accepted on every path that
validates an api_mode (``registered_api_modes``) and unregistering it degrades to chat_completions.

* ``claims(route)`` is asked once per runtime resolution (``resolve_runtime_provider``) and may rewrite
  that route's api_mode to the runtime's name. ``route`` is a ``RuntimeRoute``. First claim wins.
* ``run_turn(agent, **turn)`` runs one turn and returns the chat_completions-shaped result dict, or None to
  fall through to the generic loop. ``turn`` carries ``user_message``, ``original_user_message``,
  ``messages`` (the user message is already in it), ``effective_task_id``, ``should_review_memory`` and
  ``turn_id``. Exceptions propagate: there is no fallback to another provider unless it returns None.
* ``full_tool_schemas`` keeps the tool_search bridge out of ``agent.tools``: a runtime that declares the
  tool list once cannot inject deferred schemas mid-turn.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "AgentRuntime", "RuntimeRoute", "register_agent_runtime", "get_agent_runtime",
    "registered_runtime_modes", "claim_runtime_api_mode", "run_registered_turn", "wants_full_tool_schemas",
]


@dataclass(frozen=True)
class RuntimeRoute:
    """What a runtime claim sees: the resolved provider, the name the user asked for, the config ``model``
    section and the api_mode resolved so far."""
    provider: str
    requested_provider: str
    api_mode: str
    model_cfg: Mapping[str, Any]


@dataclass(frozen=True)
class AgentRuntime:
    name: str
    run_turn: Callable[..., Optional[dict[str, Any]]]
    claims: Optional[Callable[[RuntimeRoute], bool]] = None
    full_tool_schemas: bool = False


_RUNTIMES: dict[str, AgentRuntime] = {}

# Owned by in-tree transports/runtimes; a plugin runtime must not shadow them.
_BUILTIN_API_MODES = frozenset({
    "chat_completions", "codex_responses", "anthropic_messages", "bedrock_converse", "codex_app_server",
})


def register_agent_runtime(
    name: str,
    run_turn: Callable[..., Optional[dict[str, Any]]],
    *,
    claims: Optional[Callable[[RuntimeRoute], bool]] = None,
    full_tool_schemas: bool = False,
) -> AgentRuntime:
    """Register (or replace) the runtime ``name``. Rejects an empty name and a name an in-tree api_mode owns."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("agent runtime name must be a non-empty string")
    if not callable(run_turn):
        raise TypeError("agent runtime run_turn must be callable")
    name = name.strip()  # the guard and the registry key are the same string
    if name in _BUILTIN_API_MODES:
        raise ValueError(f"agent runtime name {name!r} collides with a built-in api_mode")
    runtime = AgentRuntime(name, run_turn, claims, bool(full_tool_schemas))
    _RUNTIMES[runtime.name] = runtime
    return runtime


# Reentrant: a provider plugin's import may ask for a runtime while discovery runs on this thread.
_DISCOVERY_LOCK = threading.RLock()


def _discover() -> None:
    """Model-provider plugins register their runtime at import; discovery is lazy, so run it first. One caller
    discovers at a time, so a second thread waits for the first to finish rather than reading a registry that
    is still filling (the providers module marks itself discovered before its imports are done)."""
    with _DISCOVERY_LOCK:
        try:
            from providers import list_providers
            list_providers()
        except Exception:  # discovery trouble must never break turn dispatch
            logger.debug("agent runtime discovery failed", exc_info=True)


def get_agent_runtime(name: str) -> Optional[AgentRuntime]:
    """The runtime registered as ``name``, or None (an unknown api_mode runs the generic loop). Built-in
    api_modes answer without touching plugin discovery: this sits on every turn."""
    if not name or name in _BUILTIN_API_MODES:
        return None
    if name not in _RUNTIMES:
        _discover()
    return _RUNTIMES.get(name)


def registered_runtime_modes() -> frozenset:
    _discover()
    return frozenset(_RUNTIMES)


def wants_full_tool_schemas(api_mode: str) -> bool:
    runtime = get_agent_runtime(api_mode) if api_mode else None
    return bool(runtime and runtime.full_tool_schemas)


def _claims(runtime: AgentRuntime, route: RuntimeRoute) -> bool:
    try:
        return runtime.claims is not None and bool(runtime.claims(route))
    except Exception:  # one broken plugin claim must not break every runtime resolution
        logger.warning("agent runtime %r claim raised; ignored", runtime.name, exc_info=True)
        return False


def claim_runtime_api_mode(
    *, provider: str, api_mode: str, model_cfg: Optional[Mapping[str, Any]], requested_provider: str = "",
) -> str:
    """``api_mode`` after the first registered runtime that claims this route, else unchanged."""
    _discover()
    route = RuntimeRoute(provider, requested_provider, api_mode, model_cfg or {})
    return next((rt.name for rt in tuple(_RUNTIMES.values()) if _claims(rt, route)), api_mode)


def run_registered_turn(agent: Any, s: Any) -> Optional[dict[str, Any]]:
    """The plugin runtime's result for this turn, or None when ``agent.api_mode`` names no runtime."""
    runtime = get_agent_runtime(getattr(agent, "api_mode", "") or "")
    return None if runtime is None else runtime.run_turn(
        agent, user_message=s.user_message, original_user_message=s.original_user_message,
        messages=s.messages, effective_task_id=s.effective_task_id,
        should_review_memory=s._should_review_memory, turn_id=s.turn_id,
    )
