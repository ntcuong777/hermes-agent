"""A gateway ``/model --global`` writes ``gateway.model`` and leaves the CLI's ``model.*`` alone.

The CLI surface (``surface="cli"``) keeps writing ``model.default`` / ``model.provider``.
"""

from __future__ import annotations

import asyncio

import hermes_yaml as yaml
from gateway.slash_commands_model import _persist_model_switch_to_config
from hermes_cli.model_switch import ModelSwitchResult

_SEED = (
    "model:\n"
    "  default: cli-model\n"
    "  provider: openrouter\n"
    "gateway:\n"
    "  model:\n"
    "    default: old-gateway-model\n"
    "    provider: openrouter\n"
)

_RESULT = ModelSwitchResult(
    success=True,
    new_model="claude-haiku",
    target_provider="anthropic",
    provider_changed=True,
    is_global=True,
)


def _load(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_gateway_global_switch_writes_gateway_model_and_leaves_model_default(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(_SEED, encoding="utf-8")

    asyncio.run(
        _persist_model_switch_to_config(
            _RESULT, path, surface="gateway", reasoning_effort="high"
        )
    )

    data = _load(path)
    assert data["model"] == {"default": "cli-model", "provider": "openrouter"}
    assert data["gateway"]["model"] == {
        "default": "claude-haiku",
        "provider": "anthropic",
        "reasoning_effort": "high",
    }


def test_gateway_global_switch_without_effort_writes_no_reasoning_key(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(_SEED, encoding="utf-8")

    asyncio.run(_persist_model_switch_to_config(_RESULT, path, surface="gateway"))

    gateway_model = _load(path)["gateway"]["model"]
    assert gateway_model["default"] == "claude-haiku"
    assert gateway_model["provider"] == "anthropic"
    assert "reasoning_effort" not in gateway_model


def test_cli_surface_still_writes_model_default(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(_SEED, encoding="utf-8")

    asyncio.run(_persist_model_switch_to_config(_RESULT, path, surface="cli"))

    data = _load(path)
    assert data["model"]["default"] == "claude-haiku"
    assert data["model"]["provider"] == "anthropic"
    assert data["gateway"]["model"]["default"] == "old-gateway-model"
