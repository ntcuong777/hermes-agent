"""Gateway route view of config.yaml: the ``gateway`` block overrides the CLI model and fallbacks.

Pure functions, no I/O. The CLI keeps reading ``model`` and the global ``fallback_providers``;
only gateway turns see the ``gateway`` block's model and fallback list. A local session the gateway
hosts (CLI, TUI, desktop) is not a gateway turn: it launches from ``local_session_config``.
"""

from __future__ import annotations

from typing import Any


def gateway_model_block(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Return ``gateway.model`` when it names both a provider and a default model, else None.

    Incomplete blocks return None so the gateway falls back to the global CLI model.
    """
    gateway = cfg.get("gateway")
    if not isinstance(gateway, dict):
        return None
    model = gateway.get("model")
    if not isinstance(model, dict):
        return None
    provider = str(model.get("provider") or "").strip()
    default = str(model.get("default") or "").strip()
    if not provider or not default:
        return None
    return {**model, "provider": provider, "default": default}


def gateway_route_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``cfg`` routed through the gateway block, or ``cfg`` itself if incomplete.

    The gateway list replaces the global ``fallback_providers`` even when empty: an empty
    gateway list means "no fallbacks", not "inherit the global list".
    """
    model = gateway_model_block(cfg)
    if model is None:
        return cfg
    gateway = cfg["gateway"]
    return {
        **cfg,
        "model": model,
        "fallback_providers": gateway.get("fallback_providers") or [],
    }


def local_session_config(cfg: dict[str, Any], host: dict[str, Any] | None) -> dict[str, Any]:
    """Return the config a local session launches from: the machine's CLI model, never the phone route.

    Since the shared-gateway merge the Ink TUI's sessions are created by the gateway, which defaulted their
    model through ``_resolve_gateway_model``, so they ran on ``gateway.model`` and ignored ``host.yaml`` (C150).
    *host* (``host.yaml``) is laid over the shared config as the CLI does (C121), and ``gateway.model`` is
    dropped: the gateway's fallbacks stay, unread by a local session. *cfg* is not mutated.
    """
    from hermes_cli.host_overlay import apply_host_overlay

    merged = apply_host_overlay(cfg, host)
    gateway = merged.get("gateway")
    if isinstance(gateway, dict) and "model" in gateway:
        merged["gateway"] = {key: value for key, value in gateway.items() if key != "model"}
    return merged


def session_reasoning_config(cfg: dict[str, Any], model: str | None):
    """The reasoning config a gateway turn runs with: on the block's own model it is the block's
    ``reasoning_effort``; a pinned other model keeps the shared resolver (per-model override, then
    ``agent.reasoning_effort``)."""
    from hermes_constants import parse_reasoning_effort, resolve_reasoning_config

    block = gateway_model_block(cfg)
    if block is not None and (not model or model == block.get("default")):
        parsed = parse_reasoning_effort(block.get("reasoning_effort"))
        if parsed is not None:
            return parsed
    return resolve_reasoning_config(cfg, model)


def match_status_route(model_name: str, provider_name: str, user_config: dict[str, Any]) -> tuple[str, str]:
    """Fallback model/provider for a status route, kept on the same route gateway turns use: a missing
    half is filled only when the known half matches this configured route."""
    model_cfg = user_config.get("model", {}) if isinstance(user_config, dict) else {}
    model_cfg = model_cfg if isinstance(model_cfg, dict) else {}
    configured_model = _resolve_gateway_model_for_route(user_config)
    configured_provider = _clean_str_for_route(model_cfg.get("provider"))
    if not model_name and (not provider_name or provider_name == configured_provider):
        model_name = configured_model
    if not provider_name and model_name == configured_model:
        provider_name = configured_provider
    return model_name, provider_name


def _resolve_gateway_model_for_route(cfg):
    from gateway.run import _resolve_gateway_model
    return _resolve_gateway_model(cfg)


def _clean_str_for_route(v):
    return str(v).strip() if isinstance(v, str) else ""
