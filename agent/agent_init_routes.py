"""Route identity helpers for agent init: known default routes per provider and the
context-pin route mismatch check. Moved out of the ``agent_init`` facade to offset its
growth (the file is over its line cap and may only go down)."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from hermes_cli.route_identity import normalize_route_base_url


def _provider_default_routes(provider: str) -> set[str]:
    """Return known exact default routes for a canonical provider id."""
    routes: set[str] = set()

    def add(value):
        route = normalize_route_base_url(value)
        if route:
            routes.add(route)

    with suppress(Exception):
        from hermes_cli.providers import HERMES_OVERLAYS, get_provider
        overlay = HERMES_OVERLAYS.get(provider)
        provider_def = get_provider(provider, allow_network=False)
        add(getattr(overlay, "base_url_override", ""))
        add(getattr(provider_def, "base_url", ""))

    with suppress(Exception):
        from providers import get_provider_profile
        add(getattr(get_provider_profile(provider), "base_url", ""))

    with suppress(Exception):
        from hermes_cli.auth import PROVIDER_REGISTRY
        from hermes_cli.models import normalize_provider as normalize_model_provider
        from hermes_cli.providers import normalize_provider as normalize_registry_provider
        for provider_id, config in PROVIDER_REGISTRY.items():
            if normalize_registry_provider(normalize_model_provider(provider_id)) == provider:
                add(getattr(config, "inference_base_url", ""))

    if provider == "gemini":
        routes.update(f"{route.rstrip('/')}/openai" for route in list(routes))
    return routes


def _context_route_mismatch(
    configured_base_url: Any, active_base_url: Any, configured_provider: Any, active_provider: Any,
    *, already_normalized: bool = False,
) -> bool:
    """Return whether a context pin's configured route differs from runtime."""
    _norm = (lambda v: str(v or "")) if already_normalized else normalize_route_base_url
    configured_route, active_route = _norm(configured_base_url), _norm(active_base_url)
    if configured_route:
        return configured_route != active_route

    configured_provider = str(configured_provider or "").strip()
    active_provider = str(active_provider or "").strip()
    if not configured_provider:
        return False
    try:
        from hermes_cli.models import normalize_provider as normalize_model_provider
        configured_provider = normalize_model_provider(configured_provider)
        active_provider = normalize_model_provider(active_provider)
    except Exception:
        configured_provider = configured_provider.lower()
        active_provider = active_provider.lower()
    with suppress(Exception):
        from hermes_cli.providers import normalize_provider as normalize_registry_provider
        configured_provider = normalize_registry_provider(configured_provider)
        active_provider = normalize_registry_provider(active_provider)

    if active_route:
        configured_routes = _provider_default_routes(configured_provider)
        if configured_routes:
            return active_route not in configured_routes
        # Named/custom providers have no catalog default routes: an empty configured URL
        # with a matching provider identity is the same route (gateway display paths
        # compare the raw empty model.base_url and must not drop model.context_length).
        return not (active_provider and configured_provider == active_provider)
    return bool(
        configured_provider and active_provider and configured_provider != active_provider
    )
