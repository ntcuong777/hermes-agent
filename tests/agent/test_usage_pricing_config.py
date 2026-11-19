"""Core does not read ``pricing:``. The config-seams plugin does, in front of this resolver.

What stays here: the config key is a known open root (``hermes config set`` accepts it),
and a pricing block does not change ``get_pricing_entry`` while the plugin is absent.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import agent.usage_pricing as pricing
from agent.usage_pricing import get_pricing_entry


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(pricing, "_models_dev_pricing_entry", lambda route: None)
    monkeypatch.setattr(pricing, "fetch_endpoint_model_metadata", lambda *a, **k: {})


def _write_config(text: str) -> None:
    home = Path(os.environ["HERMES_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(text, encoding="utf-8")
    from hermes_cli import config

    for name in (
        "invalidate_config_cache",
        "_invalidate_config_cache",
        "clear_config_cache",
    ):
        hook = getattr(config, name, None)
        if callable(hook):
            hook()


def test_a_pricing_block_does_not_change_core():
    _write_config(
        "pricing:\n"
        "  anthropic/claude-newmodel-9: {input: 3, output: 15}\n"
        "  anthropic/claude-sonnet-4-6: {input: 1, output: 1}\n"
    )

    assert get_pricing_entry("claude-newmodel-9", provider="anthropic") is None
    entry = get_pricing_entry("claude-sonnet-4-6", provider="anthropic")
    assert entry is not None and entry.source != "user_override"


def test_config_set_accepts_a_pricing_key():
    """`hermes config set pricing.<model>.input 3` must not be refused as an unknown key."""
    from hermes_cli.config import _validate_config_key

    assert _validate_config_key("pricing.anthropic/claude-newmodel-9.input")[0] is True
