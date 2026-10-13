"""Gateway route view of config.yaml: the ``gateway`` block overrides the CLI model and fallbacks.

Pure functions, no I/O. The CLI keeps reading ``model`` and the global ``fallback_providers``;
only gateway turns see the ``gateway`` block's model and fallback list.
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
