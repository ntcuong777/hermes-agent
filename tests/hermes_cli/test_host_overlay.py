"""host.yaml overlay: applied to the runtime config, never written back by save_config."""

import os
from unittest.mock import patch

import hermes_cli.config as config_mod
from hermes_cli.host_overlay import apply_host_overlay


def _shared_config() -> dict:
    return {
        "model": {"provider": "shared-provider", "default": "shared-cli-model"},
        "gateway": {"model": {"default": "shared-gateway-model"}},
        "plugins": {
            "entries": {
                "subagent-scope": {
                    "roster": {
                        "planner": {"model": "shared-planner", "tools": ["read"]}
                    },
                },
            },
        },
    }


def test_overlay_replaces_cli_model_and_roster_role_but_not_gateway():
    host = {
        "model": {"default": "host-cli-model"},
        "roster": {"planner": {"model": "host-planner"}},
    }
    shared = _shared_config()

    merged = apply_host_overlay(shared, host)

    assert merged["model"]["default"] == "host-cli-model"
    assert merged["model"]["provider"] == "shared-provider"
    roster = merged["plugins"]["entries"]["subagent-scope"]["roster"]
    assert roster["planner"] == {"model": "host-planner", "tools": ["read"]}
    assert merged["gateway"]["model"]["default"] == "shared-gateway-model"
    assert shared == _shared_config(), "input config must not be mutated"


def test_save_after_load_does_not_write_host_model_to_shared_file(tmp_path):
    shared_file = tmp_path / "config.yaml"
    shared_file.write_text("model:\n  default: shared-cli-model-x\n", encoding="utf-8")
    (tmp_path / "host.yaml").write_text(
        "model:\n  default: host-only-model\n", encoding="utf-8"
    )

    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
        config_mod._LOAD_CONFIG_CACHE.clear()
        config_mod._RAW_CONFIG_CACHE.clear()

        runtime = config_mod.load_runtime_config()
        assert runtime["model"]["default"] == "host-only-model"

        config_mod.save_config(config_mod.load_config())

    saved = shared_file.read_text(encoding="utf-8")
    assert "host-only-model" not in saved
    assert "shared-cli-model-x" in saved
