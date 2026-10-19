"""A gateway or host-file model route is published as one write, never per key.

Per-key writes could leave a half-written route (provider from the new pick, model from the old)
when one of them fails. The route is one snapshot and one publish, so a failed publish leaves the
file exactly as it was.
"""

import pytest
import hermes_yaml as yaml

from hermes_cli.model_switch import ModelSwitchResult, persist_model_selection

_RESULT = ModelSwitchResult(
    success=True,
    new_model="new-model",
    target_provider="custom:new",
    base_url="https://new.example.test/v1",
    api_mode="anthropic_messages",
    is_global=True,
)
_SEED = "model:\n  default: old-model\n  provider: openrouter\n"


def test_gateway_route_publish_failure_leaves_config_untouched(tmp_path, monkeypatch):
    import hermes_cli.config as config

    cfg = tmp_path / "config.yaml"
    cfg.write_text(_SEED, encoding="utf-8")

    def _fail(path, doc):
        raise OSError("disk full")

    monkeypatch.setattr(config, "atomic_config_replace", _fail)

    with pytest.raises(OSError, match="disk full"):
        persist_model_selection(_RESULT, cfg, surface="gateway")
    assert cfg.read_text(encoding="utf-8") == _SEED


def test_host_route_publish_failure_leaves_host_file_untouched(tmp_path, monkeypatch):
    import utils

    cfg = tmp_path / "config.yaml"
    cfg.write_text(_SEED, encoding="utf-8")
    host = tmp_path / "host.yaml"
    host_seed = "model:\n  default: machine-model\n  provider: openrouter\n"
    host.write_text(host_seed, encoding="utf-8")

    def _fail(path, doc):
        raise OSError("disk full")

    monkeypatch.setattr(utils, "atomic_roundtrip_yaml_save", _fail)

    with pytest.raises(OSError, match="disk full"):
        persist_model_selection(_RESULT, cfg, surface="cli")
    assert host.read_text(encoding="utf-8") == host_seed


def test_gateway_route_lands_as_one_nested_block(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_SEED, encoding="utf-8")

    persist_model_selection(_RESULT, cfg, surface="gateway")

    written = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert written["model"] == {"default": "old-model", "provider": "openrouter"}
    assert written["gateway"]["model"]["default"] == "new-model"
    assert written["gateway"]["model"]["provider"] == "custom:new"
