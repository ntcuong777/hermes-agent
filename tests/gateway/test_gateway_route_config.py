"""Gateway route view: the ``gateway`` block overrides model and fallbacks for gateway turns only.

The CLI keeps reading ``model`` and the global ``fallback_providers``; these tests pin that split.
"""

import copy

import pytest

from gateway.route_config import gateway_route_config
from gateway.run import _resolve_gateway_model

GLOBAL_FALLBACKS = [{"provider": "openrouter", "model": "global-fallback"}]
GATEWAY_FALLBACKS = [{"provider": "anthropic", "model": "gateway-fallback"}]


def _cfg(gateway: object = None) -> dict:
    cfg = {
        "model": {"default": "cli-model", "provider": "cli-provider"},
        "fallback_providers": copy.deepcopy(GLOBAL_FALLBACKS),
    }
    if gateway is not None:
        cfg["gateway"] = gateway
    return cfg


def test_complete_block_routes_model_and_only_gateway_fallbacks():
    cfg = _cfg(
        {
            "model": {"provider": "openai-codex", "default": "luna"},
            "fallback_providers": copy.deepcopy(GATEWAY_FALLBACKS),
        }
    )
    before = copy.deepcopy(cfg)

    route = gateway_route_config(cfg)

    assert route["model"]["provider"] == "openai-codex"
    assert route["model"]["default"] == "luna"
    assert route["fallback_providers"] == GATEWAY_FALLBACKS
    assert cfg == before, "gateway_route_config must not mutate the loaded config"
    assert cfg["model"]["default"] == "cli-model"
    assert cfg["fallback_providers"] == GLOBAL_FALLBACKS


def test_empty_gateway_fallback_list_replaces_global_list():
    cfg = _cfg(
        {
            "model": {"provider": "openai-codex", "default": "luna"},
            "fallback_providers": [],
        }
    )

    route = gateway_route_config(cfg)

    assert route["fallback_providers"] == []
    assert cfg["fallback_providers"] == GLOBAL_FALLBACKS


@pytest.mark.parametrize(
    "gateway",
    [
        None,
        "luna",
        {},
        {"model": "luna"},
        {"model": {"provider": "openai-codex"}},
        {"model": {"default": "luna"}},
        {"model": {"provider": "  ", "default": "luna"}},
    ],
)
def test_missing_or_incomplete_block_leaves_config_unchanged(gateway):
    cfg = _cfg(gateway)

    route = gateway_route_config(cfg)

    assert route is cfg
    assert route["model"]["default"] == "cli-model"
    assert route["fallback_providers"] == GLOBAL_FALLBACKS


def test_resolve_gateway_model_uses_complete_block():
    cfg = _cfg({"model": {"provider": "openai-codex", "default": "luna"}})

    assert _resolve_gateway_model(cfg) == "luna"


def test_resolve_gateway_model_falls_back_to_cli_default_when_incomplete():
    cfg = _cfg({"model": {"provider": "openai-codex"}})

    assert _resolve_gateway_model(cfg) == "cli-model"
    assert _resolve_gateway_model(_cfg()) == "cli-model"
