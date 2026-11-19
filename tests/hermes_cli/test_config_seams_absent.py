"""The config-seams seams do nothing harmful when that plugin is not installed.

These tests must not import the plugin. A fork-only run is the absent case.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway import code_skew
from hermes_cli.model_switch import ModelSwitchResult, persist_model_selection
from tools.memory_tool import _over_budget_now


def test_the_memory_budget_stub_returns_none():
    store = SimpleNamespace()
    assert _over_budget_now(store, {"action": "add", "content": "y" * 20}) is None


def test_a_gateway_surface_without_the_route_plugin_writes_model_star(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model:\n  default: old-model\n  provider: openrouter\n", encoding="utf-8")
    result = ModelSwitchResult(
        success=True, new_model="new-model", target_provider="custom:new",
        base_url="https://new.example.test/v1", api_mode="anthropic_messages", is_global=True)
    persist_model_selection(result, cfg, surface="gateway", reasoning_effort="high")
    text = cfg.read_text(encoding="utf-8")
    assert "new-model" in text
    assert "gateway:" not in text


def test_the_skew_stub_refuses_even_when_config_says_warn(monkeypatch):
    from gateway import run

    monkeypatch.setattr(code_skew, "detect_code_skew", lambda: ("aaaaaaaaaa", "bbbbbbbbbb"))
    monkeypatch.setattr(
        run, "_load_gateway_config", lambda *_path: {"gateway": {"model_switch_skew_guard": "warn"}})
    mode, skew = code_skew.model_switch_skew()
    assert mode == "refuse"
    assert skew == ("aaaaaaaaaa", "bbbbbbbbbb")


@pytest.mark.asyncio
async def test_the_compress_ack_stub_sends_nothing():
    from gateway.slash_commands_session import GatewaySessionCommandsMixin

    sent = []

    class Adapter:
        async def send(self, *args, **kwargs):
            sent.append(args)

    class Runner:
        def _delivery_adapter_for(self, source):
            return Adapter()

    event = SimpleNamespace(source=SimpleNamespace(chat_id="c"), message_id=1)
    await GatewaySessionCommandsMixin._send_compress_started_ack(Runner(), event)
    assert sent == []
