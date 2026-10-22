"""Model prices from config: ``pricing:`` in config.yaml, read before the bundled table.

A new model (or a price change) used to need a fork edit to ``_OFFICIAL_DOCS_PRICING``; the
person can now write the per-1M rates in config. Keys are ``<provider>/<model>`` or a bare
model id. Subscription routes stay $0: that is what the person actually pays.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest

from agent import usage_pricing as pricing
from agent.usage_pricing import CanonicalUsage, estimate_usage_cost, get_pricing_entry


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


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(pricing, "_models_dev_pricing_entry", lambda route: None)
    monkeypatch.setattr(pricing, "fetch_endpoint_model_metadata", lambda *a, **k: {})


def test_a_configured_price_prices_an_unknown_model():
    _write_config(
        "pricing:\n  anthropic/claude-newmodel-9:\n    input: 3\n    output: 15\n    cache_read: 0.3\n    cache_write: 3.75\n"
    )

    entry = get_pricing_entry("claude-newmodel-9", provider="anthropic")

    assert entry is not None and entry.source == "user_override"
    assert (entry.input_cost_per_million, entry.output_cost_per_million) == (
        Decimal(3),
        Decimal(15),
    )
    cost = estimate_usage_cost(
        "claude-newmodel-9",
        CanonicalUsage(input_tokens=1_000_000, output_tokens=100_000),
        provider="anthropic",
    )
    assert cost.amount_usd == Decimal("4.5")


def test_a_configured_price_wins_over_the_bundled_table():
    _write_config("pricing:\n  anthropic/claude-sonnet-5-5: {input: 1, output: 1}\n")

    entry = get_pricing_entry("claude-sonnet-5-5", provider="anthropic")

    assert entry.source == "user_override" and entry.output_cost_per_million == Decimal(
        1
    )


def test_a_bare_model_key_matches_any_provider():
    _write_config("pricing:\n  claude-newmodel-9: {input: 2, output: 8}\n")

    assert get_pricing_entry(
        "claude-newmodel-9", provider="anthropic"
    ).input_cost_per_million == Decimal(2)


def test_without_a_pricing_block_the_bundled_table_still_answers():
    _write_config("model:\n  default: claude-sonnet-5-5\n")

    entry = get_pricing_entry("claude-sonnet-5-5", provider="anthropic")

    assert entry is not None and entry.source == "official_docs_snapshot"


def test_a_malformed_rate_is_ignored_not_guessed():
    _write_config(
        "pricing:\n  anthropic/claude-newmodel-9: {input: cheap, output: 15}\n"
    )

    assert get_pricing_entry("claude-newmodel-9", provider="anthropic") is None


def test_config_set_accepts_a_pricing_key():
    """`hermes config set pricing.<model>.input 3` must not be refused as an unknown key."""
    from hermes_cli.config import _validate_config_key

    assert _validate_config_key("pricing.anthropic/claude-newmodel-9.input")[0] is True
